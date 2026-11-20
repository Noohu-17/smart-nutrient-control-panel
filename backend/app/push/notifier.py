"""The notifier (T1-41): sends Web Push for new alerts, so they reach a phone or a computer with the
app closed.

    python -m app.push.notifier      VAPID_PRIVATE_KEY and VAPID_SUBJECT set, with the API's settings

It is its own service, the one part of the back end with a route out to the internet (the push
services). The worker stays off it: it only adds a row to push_outbox when it raises an alert, in
the same transaction (app/alerts.py), and the notifier takes the rows from there. Nothing is lost
across restarts, and the outbox's key makes "one push per alert" (once more if it turns CRITICAL).

Every LOOK_EVERY_S seconds, for each queued row, oldest first:

1. The row is taken (locked, SKIP LOCKED) and the alert is checked again: still open, not
   acknowledged, still that severity, and queued less than PUSH_FOR ago (so turning pushes on
   doesn't send a backlog). Otherwise nothing is sent and the row says why (outcome).
2. Its browsers are found: every browser subscribed (push_subscription) by an active user of the
   site whose role is in that kind's notifyRoles (alerts.json; push/rules.py) and who asked for that
   severity. The row is marked SENDING and committed, so no transaction or database connection is
   held while the push services answer, and no other notifier takes the row.
3. The browsers are pushed to at once (SENDERS threads); the row then says SENT or NO_ONE. A
   notifier stopped while sending leaves the row SENDING: it isn't sent again.
4. Each endpoint is checked again before sending (push/endpoints.py). Not a push service's: the
   subscription is removed. A failed lookup or an address that isn't public: not sent, tried again
   like no answer, the subscription kept. Redirects are never followed.
5. 404 or 410 from the push service: the browser has gone, so its subscription is deleted. 429 or
   5xx, or no answer: tried again TRIES times, waiting Retry-After (at most MAX_WAIT_S) or 1 s,
   then 2 s. Anything else: given up, logged.

Pushes live TTL_S in the push service when the phone is off, then drop: a stale alarm is worse
than none. Logs name a browser by its push service and a fingerprint, never the endpoint or keys.
A file (HEARTBEAT) is touched after every row and every round, for the container's health
check: one row takes at most TRIES x TIMEOUT_S + 2 x MAX_WAIT_S = 25 s, under healthy()'s 30 s.
"""

import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, and_, any_, delete, literal, select, update

from ..config import load_settings
from ..db.engine import create_db_engine
from ..db.models import Alert, PushOutbox, PushSubscription, User
from ..packs import DATABASE_NOT_READY, PackError, load_pack
from . import endpoints
from .rules import PushRules

log = logging.getLogger("push.notifier")

LOOK_EVERY_S = 2.0
PUSH_FOR = timedelta(minutes=10)  # a row queued longer ago is skipped, not sent (TOO_OLD)
TTL_S = 15 * 60  # how long a push service keeps a push for a phone that is off
TRIES = 3
MAX_WAIT_S = 5.0
TIMEOUT_S = 5  # for one answer from a push service
SENDERS = 8  # browsers pushed to at once
ROUND = 50  # rows at most per round, so the heartbeat keeps coming
TEXT_MAX = 300  # characters of the message and next step: a push carries at most 4 KB
HEARTBEAT = Path(os.environ.get("NOTIFIER_HEARTBEAT", "/tmp/sna-notifier-alive"))


@dataclass(frozen=True)
class Answer:
    """What the push service said: its HTTP status (0 when it couldn't be reached) and Retry-After."""

    status: int
    retry_after: float | None = None


Send = Callable[[Mapping[str, Any], str, Mapping[str, str]], Answer]


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None  # an HTTP date: wait our own time instead


class WebPush:
    """The real sender: pywebpush, signed with the VAPID private key (RFC 8292), no redirects."""

    def __init__(self, private_key: str, subject: str):
        from py_vapid import Vapid02

        self.vapid = Vapid02.from_string(private_key)
        self.subject = subject

    @staticmethod
    def session():
        """A new session for each push (pushes are few, and a session isn't safe to share between
        the sending threads)."""
        import requests

        session = requests.Session()
        session.max_redirects = 0  # a redirect is an error: never follow one somewhere else
        session.trust_env = False  # no proxy from the environment
        return session

    def __call__(self, subscription: Mapping[str, Any], data: str, headers: Mapping[str, str]) -> Answer:
        import requests
        from pywebpush import WebPushException, webpush

        try:
            with self.session() as session:
                answer = webpush(
                    dict(subscription), data, vapid_private_key=self.vapid, vapid_claims={"sub": self.subject},
                    ttl=TTL_S, timeout=TIMEOUT_S, headers=dict(headers), requests_session=session,
                )
            return Answer(answer.status_code)
        except WebPushException as exc:
            if exc.response is None:
                return Answer(0)
            return Answer(exc.response.status_code, _retry_after(exc.response.headers.get("Retry-After")))
        except requests.RequestException:
            return Answer(0)


def _payload(alert) -> str:
    """What the app's service worker shows: the alert's own words, and its ID for the tap."""
    title = ("Critical" if alert.severity == "CRITICAL" else "Warning") + ": " + alert.message[:TEXT_MAX]
    return json.dumps({
        "alertId": alert.alert_id, "kind": alert.kind, "severity": alert.severity, "zone": alert.zone_id,
        "title": title, "body": alert.next_step[:TEXT_MAX],
        "raisedAt": alert.raised_at.isoformat().replace("+00:00", "Z") if alert.raised_at else None,
    })


def _forget(connection, subs) -> None:
    for sub in subs:
        connection.execute(delete(PushSubscription).where(
            PushSubscription.org_id == sub.org_id, PushSubscription.site_id == sub.site_id,
            PushSubscription.subscription_id == sub.subscription_id,
        ))


def _deliver(sub, data: str, headers: Mapping[str, str], send: Send,
             sleep: Callable[[float], None], resolve: endpoints.Resolve) -> str:
    """One push to one browser, outside any transaction: SENT, GONE (its subscription is to be
    removed) or FAILED."""
    where = endpoints.for_logs(sub.endpoint)
    if (why := endpoints.problem(sub.endpoint)) is not None:
        log.warning("not sending to %s: %s; subscription removed", where, why)
        return "GONE"
    subscription = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
    for attempt in range(1, TRIES + 1):
        # Looked up again before every try. A failed lookup or a private address isn't the
        # browser's fault (DNS down for a moment, say): not sent, tried again like no answer, and
        # the subscription is kept.
        if (why := endpoints.address_problem(sub.endpoint, resolve)) is not None:
            log.warning("not sending to %s (try %d): %s", where, attempt, why)
            answer = Answer(0)
        else:
            answer = send(subscription, data, headers)
        if 200 <= answer.status < 300:
            return "SENT"
        if answer.status in (404, 410):
            log.info("%s has gone (%d); subscription of user %s removed", where, answer.status, sub.user_id)
            return "GONE"
        if answer.status == 0 or answer.status == 429 or answer.status >= 500:
            if attempt < TRIES:
                sleep(min(MAX_WAIT_S, answer.retry_after if answer.retry_after is not None else 2.0 ** (attempt - 1)))
                continue
            log.warning("%s: no push after %d tries (last answer %d)", where, TRIES, answer.status)
            return "FAILED"
        log.warning("%s refused the push (%d)", where, answer.status)
        return "FAILED"
    return "FAILED"


def _check(connection, row, rules: PushRules, now: datetime):
    """Check a taken row's alert again: (why nothing is sent, None, []) or (None, alert, browsers)."""
    alert = connection.execute(select(Alert.__table__).where(
        Alert.org_id == row.org_id, Alert.site_id == row.site_id, Alert.alert_id == row.alert_id,
    )).first()
    if not rules.enabled:
        return "PUSH_OFF", None, []
    if alert is None or alert.cleared_at is not None:
        return "CLEARED", None, []
    if alert.acked_at is not None:
        return "ACKNOWLEDGED", None, []
    if alert.severity != row.severity:
        return "SEVERITY_CHANGED", None, []
    if now - row.queued_at > PUSH_FOR:
        return "TOO_OLD", None, []
    browsers = connection.execute(
        select(PushSubscription.__table__)
        .join(User, and_(User.org_id == PushSubscription.org_id, User.site_id == PushSubscription.site_id,
                         User.user_id == PushSubscription.user_id))
        .where(
            PushSubscription.org_id == row.org_id, PushSubscription.site_id == row.site_id,
            User.disabled_at.is_(None), User.role.in_(rules.roles_for(alert.kind)),
            literal(row.severity) == any_(PushSubscription.severities),
        )
        .order_by(PushSubscription.created_at)
    ).all()
    return None, alert, browsers


def _push_to(alert, browsers, send: Send, sleep: Callable[[float], None],
             resolve: endpoints.Resolve) -> tuple[int, list]:
    """Push one alert to its browsers at once: how many took it, and the ones that have gone."""
    if not browsers:
        return 0, []
    data = _payload(alert)
    headers = {
        "Urgency": "high" if alert.severity == "CRITICAL" else "normal",
        "Topic": alert.alert_id.replace("-", "")[:32],  # a newer push for this alert replaces an undelivered one
    }
    with ThreadPoolExecutor(max_workers=min(SENDERS, len(browsers)), thread_name_prefix="push") as pool:
        results = list(pool.map(lambda sub: _deliver(sub, data, headers, send, sleep, resolve), browsers))
    return results.count("SENT"), [sub for sub, result in zip(browsers, results, strict=True) if result == "GONE"]


def push_due(engine: Engine, org_id: str, site_id: str, rules: PushRules, send: Send, *,
             sleep: Callable[[float], None] = time.sleep, resolve: endpoints.Resolve = endpoints._addresses,
             clock: Callable[[], datetime] = lambda: datetime.now(UTC),
             beat: Callable[[], None] = lambda: None) -> int:
    """One round: push the queued rows, oldest first. Each row is taken (locked, so a second
    notifier skips it), checked and marked SENDING in one short transaction; the pushes go after
    it commits. beat() after each row. How many rows were done."""
    done = 0
    while done < ROUND:
        with engine.begin() as connection:
            row = connection.execute(
                select(PushOutbox.__table__)
                .where(PushOutbox.org_id == org_id, PushOutbox.site_id == site_id, PushOutbox.done_at.is_(None))
                .order_by(PushOutbox.queued_at).limit(1).with_for_update(skip_locked=True)
            ).first()
            if row is None:
                break
            outcome, alert, browsers = _check(connection, row, rules, clock())
            this_row = and_(PushOutbox.org_id == row.org_id, PushOutbox.site_id == row.site_id,
                            PushOutbox.alert_id == row.alert_id, PushOutbox.severity == row.severity)
            connection.execute(update(PushOutbox).where(this_row).values(
                done_at=clock(), outcome=outcome or "SENDING", sent=0))
        sent = 0
        if outcome is None:
            sent, gone = _push_to(alert, browsers, send, sleep, resolve)
            outcome = "SENT" if sent else "NO_ONE"
            with engine.begin() as connection:
                _forget(connection, gone)
                connection.execute(update(PushOutbox).where(this_row).values(outcome=outcome, sent=sent))
        log.info("alert %s (%s): %s, %d browser(s)", row.alert_id, row.severity, outcome, sent)
        beat()
        done += 1
    return done


@dataclass(frozen=True)
class VapidSettings:
    private_key: str
    subject: str  # mailto:… or https://…, for the push services to reach the sender


def load_vapid(environ: Mapping[str, str] = os.environ) -> VapidSettings:
    private_key = environ.get("VAPID_PRIVATE_KEY", "").strip()
    subject = environ.get("VAPID_SUBJECT", "").strip()
    if not private_key:
        raise ValueError("VAPID_PRIVATE_KEY must be set: python -m app.push.keys makes a pair")
    if not subject.startswith(("mailto:", "https://")):
        raise ValueError("VAPID_SUBJECT must be a mailto: or https:// contact for the push services")
    return VapidSettings(private_key, subject)


def run(sleep: Callable[[float], None] = time.sleep) -> None:
    settings = load_settings()
    vapid = load_vapid()
    send = WebPush(vapid.private_key, vapid.subject)
    engine = create_db_engine(settings)
    said = None  # what the log last said about the settings, so a change is logged once
    try:
        while True:
            try:
                # The settings each round (T1-39): the folder in dev, the newest imported version on
                # a server, kept in memory, so a new version's alerts.json counts without a restart.
                pack = load_pack(settings, engine)
                org_id, site = pack.org_id, pack.json("layout.json")["site"]
                rules = PushRules.from_pack(pack)
                now_saying = (org_id, site, pack.version, rules.enabled)
                if now_saying != said:
                    log.info("notifier for %s/%s (%s): pushes %s", org_id, site, pack.describe(),
                             "on" if rules.enabled else "off (alerts.json delivery has no WEB_PUSH): queued alerts are skipped")
                    said = now_saying
                push_due(engine, org_id, site, rules, send, sleep=sleep, beat=HEARTBEAT.touch)
            except DATABASE_NOT_READY as exc:
                log.warning("database not ready (%s); looking again in %.0f s",
                            str(getattr(exc, "orig", exc)).partition("\n")[0], LOOK_EVERY_S)
            except PackError as exc:
                log.warning("no farm settings yet (%s); looking again in %.0f s", exc, LOOK_EVERY_S)
            except Exception:  # one bad round must never stop the notifier
                log.exception("push round failed; looking again in %.0f s", LOOK_EVERY_S)
            HEARTBEAT.touch()
            sleep(LOOK_EVERY_S)
    finally:
        engine.dispose()


def healthy(max_age_s: float = 30.0) -> bool:
    """For the container's health check: a round finished within max_age_s."""
    try:
        return time.time() - HEARTBEAT.stat().st_mtime < max_age_s
    except OSError:
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run()
    except KeyboardInterrupt:
        log.info("notifier stopped")


if __name__ == "__main__":
    main()
