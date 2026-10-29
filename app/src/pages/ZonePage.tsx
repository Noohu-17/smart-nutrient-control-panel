import { useParams } from "react-router-dom";
import { ZONES } from "../app/nav";
import { Placeholder } from "./Placeholder";

export function ZonePage() {
  const { zoneId } = useParams();
  const zone = ZONES.find((z) => z.id === zoneId);
  if (!zone) return <Placeholder title="Zone not found" note={`There is no zone called "${zoneId}".`} />;
  return <Placeholder title={zone.name} />;
}
