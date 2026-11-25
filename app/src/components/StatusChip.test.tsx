import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusChip } from "./StatusChip";

describe("StatusChip", () => {
  it("shows its text with the tone's colour", () => {
    render(<StatusChip tone="off">Offline</StatusChip>);
    expect(screen.getByText("Offline")).toHaveClass("chip", "chip-off");
  });
});
