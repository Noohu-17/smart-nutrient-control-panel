import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LockedButton } from "./LockedButton";

describe("LockedButton", () => {
  it("is shown disabled with a lock and a one-line reason, and does nothing when clicked", async () => {
    const onClick = vi.fn();
    render(
      <LockedButton reason="Viewers can’t dose" onClick={onClick}>
        Dose
      </LockedButton>,
    );
    const button = screen.getByRole("button", { name: "Dose" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("Viewers can’t dose");
    expect(button.querySelector("svg")).not.toBeNull(); // the lock icon
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("works as a normal button when there is no reason", async () => {
    const onClick = vi.fn();
    render(<LockedButton onClick={onClick}>Dose</LockedButton>);
    await userEvent.click(screen.getByRole("button", { name: "Dose" }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
