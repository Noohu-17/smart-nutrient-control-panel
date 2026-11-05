import { useCommand, useStopZone } from "../../api/hooks";
import type { Command, CommandLine } from "../../api/types";
import { StatusChip, type ChipTone } from "../../components/StatusChip";
import {
  COMMAND_VIEW,
  isFinalCommand,
  isMixing,
  LINE_STATE_TEXT,
  lineName,
  MIXING_VIEW,
  reasonText,
  stopsAreOffered,
} from "../../config/dosing";

const shown = (value: number | null | undefined) => value ?? "--";

function LineRow({ line, command }: { line: CommandLine; command: Command }) {
  const unknown = command.state === "UNKNOWN";
  // a line short of its amount is reported SHORT by the controller itself (§6.6)
  const tone: ChipTone = unknown || line.state === "SHORT" ? "warn" : line.state === "DONE" ? "ok" : "neutral";
  const word = unknown ? "Unknown" : LINE_STATE_TEXT[line.state];
  return (
    <li>
      <span>{lineName(line.line)}</span>
      <span>
        {unknown ? "Last reported " : ""}
        {line.deliveredMl} / {line.requestedMl} mL
      </span>
      <StatusChip tone={tone}>{word}</StatusChip>
    </li>
  );
}

function Result({ command }: { command: Command }) {
  const view = COMMAND_VIEW[command.state];
  const reason = reasonText(command.reason);
  if (command.state === "UNKNOWN")
    return (
      <div className="banner warn" role="status">
        <b>Nothing is confirmed.</b> The amounts below are only what the farm last reported. The zone stays
        blocked and this will update when the farm reconnects. The dose is never sent again.
      </div>
    );
  if (command.state === "COMPLETED" && isMixing(command))
    return (
      <div className="banner info" role="status">
        <b>Every line delivered. The reservoir is mixing before it is re-measured.</b> Before: EC{" "}
        {shown(command.before?.ec)} · pH {shown(command.before?.ph)}. The new values appear here when
        mixing ends.
      </div>
    );
  if (command.state === "COMPLETED")
    return (
      <div className="banner info" role="status">
        <b>Every line delivered and the reservoir was re-measured.</b> EC {shown(command.before?.ec)} →{" "}
        {shown(command.after?.ec)} · pH {shown(command.before?.ph)} → {shown(command.after?.ph)}.
      </div>
    );
  if (command.state === "FAILED")
    return (
      <div className="banner crit" role="alert">
        <b>
          {command.device}: {reason}
        </b>{" "}
        Later lines were skipped and mixing did not start.
      </div>
    );
  if (command.state === "REJECTED")
    return (
      <div className="banner crit" role="alert">
        <b>Refused: {reason}</b> Nothing ran, and it is not tried again by itself.
      </div>
    );
  if (command.state === "PARTIAL" || command.state === "STOPPED")
    return (
      <div className={`banner ${view.tone === "crit" ? "crit" : "warn"}`} role="status">
        <b>{reason}</b> Check the amounts below before dosing again.
      </div>
    );
  if (command.state === "EXPIRED")
    return (
      <div className="banner warn" role="status">
        <b>Nothing ran.</b> Start a new dose if it is still needed.
      </div>
    );
  return null;
}

/** A dose after it is sent. Shows only what the server reports; it never moves a state by itself. */
export function CommandStatus({
  cmdId,
  zoneId,
  onClose,
}: {
  cmdId: string;
  zoneId: string;
  onClose: () => void;
}) {
  const query = useCommand(cmdId);
  const stop = useStopZone();
  const command = query.data;

  if (!command) {
    return query.isError ? (
      <p className="banner crit" role="alert">
        Couldn’t read the dose status: {query.error.message}. It is not sent again; check before trying.
      </p>
    ) : (
      <p className="dose-note">Reading the dose status…</p>
    );
  }

  const mixing = command.state === "COMPLETED" && isMixing(command);
  const view = mixing ? MIXING_VIEW : COMMAND_VIEW[command.state];
  const running = command.state === "EXECUTING";
  const lines = command.lines ?? [];
  return (
    <>
      <div className="cmd-head">
        <b>{view.label}</b>
        <StatusChip tone={view.tone}>{view.word}</StatusChip>
      </div>

      <Result command={command} />

      <ul className="cmd-lines" aria-label="Dose lines">
        {lines.map((line) => (
          <LineRow key={line.line} line={line} command={command} />
        ))}
        {(running || mixing) && (
          <li>
            <span>Mix and re-measure</span>
            <span />
            <StatusChip>{mixing ? "Running" : "Queued"}</StatusChip>
          </li>
        )}
      </ul>

      {stop.isError && (
        <p className="banner crit" role="alert">
          Stop did not reach the server: {stop.error.message}. Try again or use the E-STOP.
        </p>
      )}
      {query.isError && (
        <p className="banner warn" role="status">
          Couldn’t refresh the status: {query.error.message}. This is the last known state.
        </p>
      )}
      {stop.isSuccess && stopsAreOffered(command.state) && (
        <p className="dose-note" role="status">
          Stop sent.
        </p>
      )}

      <div className="dose-actions">
        {stopsAreOffered(command.state) && (
          <button
            type="button"
            className="btn danger"
            disabled={stop.isPending}
            onClick={() => stop.mutate(zoneId)}
          >
            Stop dose
          </button>
        )}
        {isFinalCommand(command.state) && (
          <button type="button" className="btn primary" onClick={onClose}>
            {view.closeWith}
          </button>
        )}
      </div>
    </>
  );
}
