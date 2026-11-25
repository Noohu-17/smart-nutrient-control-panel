import type { Farm } from "../../api/types";

const ZONE_W = 320;
const WALL_W = 48; // room for the pads on one side and the fans on the other
const ZONE_HEAD = 36; // zone name above its benches
const ROW_H = 70;
const ROW_GAP = 10;
const INSET = 12;
const FOOT = 26; // wall labels under the house
const PAD_W = 12;
const FAN_R = 10;
const LABEL_W = 62; // bench ID at the start of each row

/** 2D polyhouse plan drawn only from the layout, for any number of zones, benches, channels
    and fans: zones side by side, benches as rows, one line per channel, pads on one wall and
    fans on the other. No live data yet. */
export function FarmPlan({ farm }: { farm: Farm }) {
  const { zones, climate } = farm;
  const rows = Math.max(1, ...zones.map((z) => z.benches.length));
  const houseW = zones.length * ZONE_W;
  const houseH = ZONE_HEAD + rows * ROW_H + (rows - 1) * ROW_GAP + INSET;
  const width = houseW + WALL_W * 2;
  const height = houseH + FOOT;
  const padX = WALL_W - PAD_W - 8;
  const fanX = WALL_W + houseW + WALL_W / 2;

  return (
    <svg
      className="farm-plan"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${farm.name} plan`}
    >
      <rect className="plan-house" x={WALL_W} y={0} width={houseW} height={houseH} rx={6} />

      {climate.padPumps.length > 0 && (
        <>
          <rect className="plan-pad" x={padX} y={INSET} width={PAD_W} height={houseH - INSET * 2} rx={3}>
            <title>{`Cooling pads · ${climate.padPumps.join(", ")}`}</title>
          </rect>
          <text className="plan-label" x={padX + PAD_W / 2} y={height - 8} textAnchor="middle">
            Pads
          </text>
        </>
      )}

      {climate.fans.map((fan, i) => (
        <circle
          key={fan}
          className="plan-fan"
          cx={fanX}
          cy={((i + 1) * houseH) / (climate.fans.length + 1)}
          r={FAN_R}
        >
          <title>{`Exhaust fan ${fan}`}</title>
        </circle>
      ))}
      {climate.fans.length > 0 && (
        <text className="plan-label" x={fanX} y={height - 8} textAnchor="middle">
          Fans
        </text>
      )}

      {zones.map((zone, zi) => {
        const x = WALL_W + zi * ZONE_W + INSET;
        const rowW = ZONE_W - INSET * 2;
        return (
          <g key={zone.id}>
            {zi > 0 && <line className="plan-split" x1={x - INSET} x2={x - INSET} y1={0} y2={houseH} />}
            <text className="plan-zone" x={x} y={24}>
              {zone.name}
            </text>
            {zone.benches.map((bench, bi) => {
              const y = ZONE_HEAD + bi * (ROW_H + ROW_GAP);
              return (
                <g key={bench.id}>
                  <title>{`${bench.id} · ${bench.channels} channels · ${bench.lengthM} m`}</title>
                  <rect className="plan-bench" x={x} y={y} width={rowW} height={ROW_H} rx={3} />
                  <text className="plan-label" x={x + 8} y={y + ROW_H / 2 + 4}>
                    {bench.id}
                  </text>
                  {Array.from({ length: bench.channels }, (_, ci) => {
                    const cy = y + ((ci + 1) * ROW_H) / (bench.channels + 1);
                    return (
                      <line
                        key={ci}
                        className="plan-channel"
                        x1={x + LABEL_W}
                        x2={x + rowW - 8}
                        y1={cy}
                        y2={cy}
                      />
                    );
                  })}
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}
