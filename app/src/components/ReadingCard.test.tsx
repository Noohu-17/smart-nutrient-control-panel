import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ZONE_READINGS } from "../config/readings";
import { ReadingCard } from "./ReadingCard";

const EC = ZONE_READINGS.find((r) => r.key === "ec")!;
const target = { min: 1.2, max: 1.8 };
const card = () => screen.getByRole("group", { name: "EC" });

describe("ReadingCard", () => {
  it("is green inside the target band, amber outside it and grey when stale", () => {
    const { rerender } = render(<ReadingCard spec={EC} value={1.62} quality="OK" target={target} />);
    expect(card()).toHaveClass("reading-ok");
    expect(card()).toHaveTextContent("1.62mS/cm");
    expect(card()).toHaveTextContent("Target 1.20–1.80");
    expect(card()).toHaveTextContent("In target");

    rerender(<ReadingCard spec={EC} value={2.1} quality="OK" target={target} />);
    expect(card()).toHaveClass("reading-warn");
    expect(card()).toHaveTextContent("Too high");

    rerender(<ReadingCard spec={EC} value={0.95} quality="OK" target={target} />);
    expect(card()).toHaveClass("reading-warn");
    expect(card()).toHaveTextContent("Too low");

    rerender(<ReadingCard spec={EC} value={1.62} quality="STALE" target={target} />);
    expect(card()).toHaveClass("reading-off");
    expect(card()).toHaveTextContent("No fresh sample");
  });

  it('shows "--" with the reason when the sensor sends null', () => {
    render(<ReadingCard spec={EC} value={null} quality="FAULT" />);
    expect(card()).toHaveClass("reading-off");
    expect(card()).toHaveTextContent("--");
    expect(card()).toHaveTextContent("Sensor fault");
  });
});
