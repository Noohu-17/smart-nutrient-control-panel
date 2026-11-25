import { useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { SimulatorPage } from "./SimulatorPage";

/** Left pane width in percent; the divider stays between these bounds so neither pane disappears. */
const MIN = 20;
const MAX = 80;
const SPLIT_KEY = "snm.split";
const clamp = (n: number) => Math.min(MAX, Math.max(MIN, Math.round(n)));

function savedSplit() {
  try {
    const saved = Number(localStorage.getItem(SPLIT_KEY));
    return saved ? clamp(saved) : 50;
  } catch {
    return 50;
  }
}

/** The control panel and the Simulator on one screen: the page on the left, the farm on the right.
    One page, so both run on the same API answers: a dose started on the left runs in the farm on
    the right, and a value held on the right shows on the left. Drag the divider to resize. */
export function SideBySide({ children }: { children: ReactNode }) {
  const panes = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const [split, setSplit] = useState(savedSplit);
  const save = (s: number) => {
    try {
      localStorage.setItem(SPLIT_KEY, String(s));
    } catch {
      /* storage blocked: the split still applies now */
    }
  };
  const choose = (s: number) => {
    const next = clamp(s);
    setSplit(next);
    save(next);
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const box = panes.current?.getBoundingClientRect();
    if (dragging && box?.width) setSplit(clamp(((e.clientX - box.left) / box.width) * 100));
  };
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    e.currentTarget.releasePointerCapture(e.pointerId);
    setDragging(false);
    save(split);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    const step = e.shiftKey ? 10 : 2;
    const keys: Record<string, number> = {
      ArrowLeft: split - step,
      ArrowRight: split + step,
      Home: MIN,
      End: MAX,
    };
    if (e.key in keys) {
      e.preventDefault();
      choose(keys[e.key]);
    }
  };

  return (
    <div
      ref={panes}
      className={`sbs-panes${dragging ? " dragging" : ""}`}
      style={{ gridTemplateColumns: `minmax(0, ${split}fr) auto minmax(0, ${100 - split}fr)` }}
    >
      <div className="sbs-pane" aria-label="Control panel">
        {children}
      </div>
      <div
        className="sbs-divider"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize panes"
        aria-valuemin={MIN}
        aria-valuemax={MAX}
        aria-valuenow={split}
        aria-valuetext={`Control panel ${split}%, simulator ${100 - split}%`}
        tabIndex={0}
        title="Drag to resize · double-click for 50 / 50"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={() => choose(50)}
        onKeyDown={onKeyDown}
      />
      <div className="sbs-pane" aria-label="Virtual farm simulator">
        <SimulatorPage />
      </div>
    </div>
  );
}
