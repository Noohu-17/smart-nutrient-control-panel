import { useDevices, useFarm } from "../../api/hooks";
import type { Device } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { StatusChip } from "../../components/StatusChip";
import { KIND_TEXT, lastSeenText } from "../../config/devices";

/** /devices · every controller the site runs, from GET /api/devices: online or offline
    (3 minutes without a message, contract §14) and when it was last heard from. */
export function Devices() {
  const devices = useDevices();
  const farm = useFarm();
  const zoneName = (d: Device) => farm.data?.zones.find((z) => z.id === d.zone)?.name ?? d.zone;
  const online = devices.data?.filter((d) => d.online).length ?? 0;

  return (
    <>
      <div className="phead">
        <h1>Devices</h1>
        {devices.data && devices.data.length > 0 && (
          <p>
            {online} of {devices.data.length} online
          </p>
        )}
      </div>
      {devices.isPending ? (
        <EmptyState title="Loading the devices…" />
      ) : devices.isError ? (
        <EmptyState title="Couldn’t load the devices">{devices.error.message}</EmptyState>
      ) : devices.data.length === 0 ? (
        <EmptyState title="No devices yet">Devices appear here once the farm layout lists them.</EmptyState>
      ) : (
        <ul className="devices">
          {devices.data.map((d) => (
            <li key={d.deviceId} className={d.online ? undefined : "offline"} aria-label={d.deviceId}>
              <div>
                <b>{d.deviceId}</b>
                <span>
                  {KIND_TEXT[d.kind] ?? d.kind}
                  {d.zone && ` · ${zoneName(d)}`}
                </span>
              </div>
              <div className="device-state">
                <StatusChip tone={d.online ? "ok" : "off"}>{d.online ? "Online" : "Offline"}</StatusChip>
                <span>{d.lastSeen ? `Last seen ${lastSeenText(d.lastSeen)}` : lastSeenText(null)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
