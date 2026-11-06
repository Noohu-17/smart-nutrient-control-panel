import { usePower } from "../../api/hooks";
import type { Farm, PowerState } from "../../api/types";
import { StatusChip, type ChipTone } from "../../components/StatusChip";

type Item = { label: string; tone: ChipTone; text: string; detail?: string };

const MAINS: Record<PowerState["mains"], [ChipTone, string]> = {
  OK: ["ok", "On"],
  LOST: ["crit", "Lost"],
  PHASE_FAULT: ["warn", "Phase fault"],
};

const GENERATOR: Record<NonNullable<PowerState["generator"]>, [ChipTone, string]> = {
  STOPPED: ["neutral", "Standby"],
  STARTING: ["warn", "Starting"],
  RUNNING: ["warn", "Running"],
  FAULT: ["crit", "Fault"],
};

const volts = (v: number | null) => (v === null ? "--" : String(v));

/** Mains, generator and UPS in plain words. An item the layout doesn't have is left out (contract §6.3). */
function powerItems(power: PowerState | undefined, layout: Farm["power"], offline: boolean): Item[] {
  const unknown = (label: string): Item => ({ label, tone: "off", text: offline ? "No data" : "Loading" });
  const items: Item[] = [];

  if (!power) items.push(unknown("Mains"));
  else {
    const [tone, text] = MAINS[power.mains];
    const { l1V, l2V, l3V } = power.phases;
    items.push({ label: "Mains", tone, text, detail: `${volts(l1V)} / ${volts(l2V)} / ${volts(l3V)} V` });
  }

  if (layout.generator) {
    if (!power?.generator) items.push(unknown("Generator"));
    else {
      const [tone, text] = GENERATOR[power.generator];
      items.push({ label: "Generator", tone, text });
    }
  }

  if (layout.ups) {
    if (!power?.ups) items.push(unknown("UPS"));
    else {
      const { onBattery, batteryPct, runtimeMin } = power.ups;
      items.push({
        label: "UPS",
        tone: onBattery ? "warn" : "ok",
        text: onBattery ? "On battery" : "Ready",
        detail: `${batteryPct}% · ${runtimeMin} min`,
      });
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
