import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderAt } from "../test/render";

const button = (name: string) => screen.getByRole("button", { name });

/** The only way to change role: sign out, then sign in as someone else (back on the same screen). */
async function signInAs(role: string) {
  await userEvent.click(button("Sign out"));
  await userEvent.click(await screen.findByRole("button", { name: new RegExp(`^${role}`) }));
  await screen.findByRole("button", { name: "Sign out" });
}

describe("Roles", () => {
  it("asks who you are first, then opens the Overview as that role", async () => {
    renderAt("/", { role: null });
    await userEvent.click(await screen.findByRole("button", { name: /Viewer/ }));
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("banner")).toHaveTextContent("Signed in as Viewer");

    await userEvent.click(button("Sign out"));
    expect(await screen.findByRole("heading", { name: "Sign in as" })).toBeInTheDocument();
  });

  it("keeps the signed-in role: there is no way to switch it without signing out", async () => {
    renderAt("/", { role: "Owner" });
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("banner")).toHaveTextContent("Signed in as Owner");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("signing in as another role changes which zone actions are locked", async () => {
    renderAt("/zone/Z1");
    await screen.findByRole("heading", { level: 1, name: "Zone A" });
    const actions = ["Dose", "Refill", "Change recipe", "Clear fault"];
    const locked = () => actions.filter((name) => button(name).hasAttribute("disabled"));

    expect(locked()).toEqual([]); // Owner

    await signInAs("Agronomist");
    expect(locked()).toEqual(["Clear fault"]);
    expect(button("Clear fault")).toHaveAccessibleDescription("Only Owners clear a latched fault");

    await signInAs("Operator");
    expect(locked()).toEqual(["Change recipe", "Clear fault"]);

    await signInAs("Viewer");
    expect(locked()).toEqual(actions);
    expect(button("Dose")).toHaveAccessibleDescription("Viewers can’t operate equipment");
  });

  it("locks Acknowledge and Export for a Viewer only", async () => {
    renderAt("/alerts", { role: "Viewer" });
    expect(await screen.findByRole("button", { name: "Acknowledge all" })).toBeDisabled();
    await signInAs("Operator");
    await userEvent.click(button("Acknowledge all"));
    expect(screen.getByRole("status")).toHaveTextContent("Coming in a later task");
  });

  it("opens every screen in the navigation", async () => {
    renderAt("/");
    const nav = within(screen.getByRole("navigation", { name: "Main" }));
    await nav.findByRole("link", { name: "Zone B" }); // zone links come from /api/farm
    const links = nav.getAllByRole("link").map((a) => a.textContent!);
    expect(links).toEqual(["Overview", "Zone A", "Zone B", "Climate", "Alerts", "History", "Simulator"]);

    for (const name of links) {
      await userEvent.click(nav.getByRole("link", { name }));
      expect(await screen.findByRole("heading", { level: 1, name })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Page not found" })).not.toBeInTheDocument();
    }
  });
});
