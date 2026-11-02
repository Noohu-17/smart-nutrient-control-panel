import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("shows a title and the explanation", () => {
    render(<EmptyState title="No alerts">Nothing needs attention right now.</EmptyState>);
    expect(screen.getByRole("heading", { name: "No alerts" })).toBeInTheDocument();
    expect(screen.getByText("Nothing needs attention right now.")).toBeInTheDocument();
  });
});
