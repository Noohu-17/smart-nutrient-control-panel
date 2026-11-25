"""Which push endpoints the server will ever send to (T1-41).

The endpoint comes from the browser, so it is checked like any input, when a subscription is saved
and again before every send: https only, on port 443, on one of the push services' own hosts, with
no user name or password in it, and never to an address inside a private network. The notifier is
on the server's internal network too, so http://db:5432 or https://10.0.0.5/ must never work.
"""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

MAX_ENDPOINT = 2048  # characters; real endpoints are a few hundred
# The browsers' push services: Chrome, Edge on Android and others (FCM), Safari (Apple),
# Firefox (Mozilla), Edge on Windows (WNS). A host is one of these, or a name under one.
PUSH_HOSTS = ("fcm.googleapis.com", "push.apple.com", "push.services.mozilla.com", "notify.windows.com")

Resolve = Callable[[str], list[str]]


def _addresses(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


def problem(endpoint: str) -> str | None:
    """Why this endpoint isn't a push service's, or None when it is (the address is checked by
    public_address, just before sending)."""
    if len(endpoint) > MAX_ENDPOINT:
        return "too long"
    try:
        url = urlsplit(endpoint)
        port = url.port
    except ValueError:
        return "not a URL"
    if url.scheme != "https":
        return "not https"
    if url.username is not None or url.password is not None:
        return "has a user name or password"
    if port not in (None, 443):
        return "not on port 443"
    host = (url.hostname or "").rstrip(".")
    try:
        ipaddress.ip_address(host)
        return "an address, not a push service's name"
    except ValueError:
        pass
    if not any(host == known or host.endswith("." + known) for known in PUSH_HOSTS):
        return "not a known push service"
    return None


def address_problem(endpoint: str, resolve: Resolve = _addresses) -> str | None:
    """None when every address the endpoint's host has is a public one: never a private, loopback,
    link-local or reserved address, so no name can be pointed inside the network. Otherwise why
    not: "lookup failed" (DNS down for a moment, say) or "not a public address"."""
    host = (urlsplit(endpoint).hostname or "").rstrip(".")
    try:
        addresses = [ipaddress.ip_address(a.split("%")[0]) for a in resolve(host)]
    except (OSError, ValueError):
        return "lookup failed"
    if not addresses:
        return "lookup failed"
    if not all(a.is_global and not a.is_multicast for a in addresses):
        return "not a public address"
    return None


def public_address(endpoint: str, resolve: Resolve = _addresses) -> bool:
    return address_problem(endpoint, resolve) is None


def for_logs(endpoint: str) -> str:
    """The host and a short fingerprint, never the whole endpoint: its path identifies the browser."""
    import hashlib

    return f"{urlsplit(endpoint).hostname}#{hashlib.sha256(endpoint.encode()).hexdigest()[:8]}"
