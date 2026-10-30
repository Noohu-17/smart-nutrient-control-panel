import { useParams } from "react-router-dom";
import { useFarm } from "../api/hooks";
import { Placeholder } from "./Placeholder";

export function ZonePage() {
  const { zoneId } = useParams();
  const farm = useFarm();
  if (farm.isPending) return <Placeholder title="Zone" note="Loading the farm…" />;
  const zone = farm.data?.zones.find((z) => z.id === zoneId);
  if (!zone) return <Placeholder title="Zone not found" note={`There is no zone called "${zoneId}".`} />;
  return <Placeholder title={zone.name} />;
}
