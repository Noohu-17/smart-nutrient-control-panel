import { usePower } from "../../api/hooks";
import type { Farm, PowerStatus } from "../../api/types";
import { StatusChip, type ChipTone } from "../../components/StatusChip";
import { QUALITY_TEXT } from "../../config/readings";

type Item = { label: string; tone: ChipTone; text: string; detail?: string };

const MAINS: Record<NonNullable<PowerStatus["mains"]>, [ChipTone, string]> = {
  OK: ["ok", "On"],
  LOST: ["crit", "Lost"],
  PHASE_FAULT: ["warn", "Phase fault"],
};

const GENERATOR: Record<NonNullable<PowerStatus["generator"]>, [ChipTone, string]> = {
  STOPPED: ["neutral", "Standby"],
  STARTING: ["warn", "Starting"],
  RUNNING: ["warn", "Running"],
  FAULT: ["crit", "Fault"],
};

const volts = (v: number | null) => (v === null ? "--" : String(v));
const shown = (v: number | null, unit: string) => (v === null ? "--" : `${v}${unit}`);

/** Mains, generator and UPS in plain words. An item the layout doesn't have is left out (contract §11.2).
    Until the monitor first reports there is nothing to say; once it is offline or its message is old,
    the last values are shown grey and marked, never as the farm's state now. */
function powerItems(power: PowerStatus | undefined, layout: Farm["power"], failed: boolean): Item[] {
  const unknown = (label: string): Item => ({
    label,
    tone: "off",
    text: failed || power ? "No data" : "Loading",
  });
  const old = power !== undefined && (!power.online || Object.values(power.quality).includes("STALE"));
  const item = (label: string, [tone, text]: [ChipTone, string], detail?: string): Item =>
    old
      ? { label, tone: "off", text, detail: detail ? `${detail} · ${QUALITY_TEXT.STALE}` : QUALITY_TEXT.STALE }
      : { label, tone, text, detail };
  const items: Item[] = [];

  if (!power?.mains) items.push(unknown("Mains"));
  else {
    const { l1V, l2V, l3V } = power.phases;
    items.push(item("Mains", MAINS[power.mains], `${volts(l1V)} / ${volts(l2V)} / ${volts(l3V)} V`));
  }

  if (layout.generator) {
    if (!power?.generator) items.push(unknown("Generator"));
    else items.push(item("Generator", GENERATOR[power.generator]));
  }

  if (layout.ups) {
    if (!power?.ups) items.push(unknown("UPS"));
    else {
      const { onBattery, batteryPct, runtimeMin } = power.ups;
      items.push(
        item(
          "UPS",
          onBattery ? ["warn", "On battery"] : ["ok", "Ready"],
          `${shown(batteryPct, "%")} · ${shown(runtimeMin, " min")}`,
        ),
      );
    }
  }
  return items;
}

/** Overview strip: where the farm's power comes from right now. */
export function PowerStrip({ layout }: { layout: Farm["power"] }) {
  const power = usePower();
  const items = powerItems(power.data, layout, power.isError);
  return (
    <section className="power-strip" aria-label="Power">
      {items.map((item) => (
        <div key={item.label} className="power-item" role="group" aria-label={item.label}>
          <span className="power-label">{item.label}</span>
          <StatusChip tone={item.tone}>{item.text}</StatusChip>
          {item.detail && <span className="power-detail">{item.detail}</span>}
        </div>
      ))}
    </section>
  );
}
