import { useResumeZone } from "../../api/hooks";
import { can, WHY } from "../../auth/roles";
import { useSession } from "../../auth/session";
import { LockedButton } from "../../components/LockedButton";
import type { ZoneStatus } from "./useZoneStatus";

/** Resume, inside the zone's stopped or E-STOP alert. A stopped zone stays stopped until a person
    has checked it and presses Resume (§6.8, §7.8). Owner, Agronomist and Operator may resume; a
    Viewer sees why not. */
export function ResumeZone({ zoneId, status }: { zoneId: string; status: ZoneStatus }) {
  const { role } = useSession();
  const resume = useResumeZone();
  const { t } = status;
  if (t?.stopped !== true) return null;

  const allowed = role !== null && can(role, "operate");
  const reason = !allowed
    ? WHY.operate
    : t.estop
      ? "Release the E-STOP first"
      : status.offline
        ? "The controller is offline"
        : resume.isPending
          ? "Sending…"
          : undefined;
  return (
    <>
      <div className="dose-actions">
        <LockedButton reason={reason} onClick={() => resume.mutate(zoneId)}>
          Resume
        </LockedButton>
      </div>
      {resume.isError && (
        <p className="dose-note" role="alert">
          Resume was not sent: {resume.error.message}
        </p>
      )}
    </>
  );
}
