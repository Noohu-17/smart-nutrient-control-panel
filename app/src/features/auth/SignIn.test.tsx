// T2-27 · sign-in against the server (T1-35): here the fake API, which answers in its shapes and
// checks its rules (mocks/session.ts). With mocks off the same screens use the real back end.
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeQueryClient } from "../../app/queryClient";
import { routes } from "../../app/routes";
import { SessionProvider } from "../../auth/SessionProvider";
import { FAKE_CODE, FAKE_PASSWORD, fakeSignIn, resetSession } from "../../mocks/session";
import { renderAt } from "../../test/render";

afterEach(() => {
  vi.unstubAllEnvs();
});

/** The app as main.tsx starts it: the session comes from GET /api/me. */
function renderFresh(path: string) {
  return render(
    <QueryClientProvider client={makeQueryClient({ retry: false })}>
      <SessionProvider>
        <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />
      </SessionProvider>
    </QueryClientProvider>,
  );
}

/** Signed out, with demo mode off: the email and password form. */
async function passwordForm(path = "/") {
  vi.stubEnv("VITE_DEMO_MODE", "false");
  fakeSignIn(null);
  const user = userEvent.setup();
  renderFresh(path);
  await screen.findByRole("heading", { name: "Sign in" });
  const signIn = async (email: string, password = FAKE_PASSWORD) => {
    await user.type(screen.getByLabelText("Email"), email);
    await user.type(screen.getByLabelText("Password"), password);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
  };
  return { user, signIn };
}

const topBar = () => within(document.querySelector<HTMLElement>(".topbar")!);

describe("Sign-in with an account", () => {
  it("signs an Operator in, shows their name and the server's role, and lets them dose", async () => {
    const { user, signIn } = await passwordForm("/zone/Z1");
    await signIn("operator@farm.test");

    const card = within((await screen.findByRole("heading", { name: "Manual dose" })).closest("section")!);
    expect(topBar().getByText("Ravi Operator")).toBeInTheDocument();
    expect(topBar().getByText(/· Operator/)).toBeInTheDocument();

    await user.type(card.getByLabelText("Stock A"), "120");
    await user.click(card.getByRole("button", { name: "Review dose" }));
    await user.click(await screen.findByRole("checkbox", { name: /I have checked the quantities/ }));
    await user.click(screen.getByRole("button", { name: "Start dose" }));
    expect(await screen.findByText("Sent — waiting for the farm")).toBeInTheDocument();
  });

  it("locks the dose for a Viewer, as the server says", async () => {
    const { signIn } = await passwordForm("/zone/Z1");
    await signIn("viewer@farm.test");

    const card = within((await screen.findByRole("heading", { name: "Manual dose" })).closest("section")!);
    expect(topBar().getByText(/· Viewer/)).toBeInTheDocument();
    expect(card.getByLabelText("Stock A")).toBeDisabled();
    expect(card.getByRole("button", { name: "Review dose" })).toHaveAccessibleDescription(
      "Viewers can’t operate equipment",
    );
  });

  it("says so for a wrong password, and stays on the sign-in page", async () => {
    const { signIn } = await passwordForm();
    await signIn("operator@farm.test", "not-the-password");
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password");
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("signs out: back to the sign-in page, and the server's session has ended", async () => {
    const { user, signIn } = await passwordForm();
    await signIn("operator@farm.test");
    await screen.findByRole("heading", { name: "Overview" });

    await user.click(topBar().getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect((await fetch("/api/me")).status).toBe(401);
  });
});

describe("Two-factor for the Owner", () => {
  it("asks for the authenticator code after the password, and says when it is wrong", async () => {
    const { user, signIn } = await passwordForm();
    await signIn("owner@farm.test");

    await screen.findByRole("heading", { name: "Two-factor code" });
    await user.type(screen.getByLabelText("Code"), "000000");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That code is not right");

    await user.clear(screen.getByLabelText("Code"));
    await user.type(screen.getByLabelText("Code"), FAKE_CODE);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(topBar().getByText("Farm Owner")).toBeInTheDocument();
  });

  it("goes back to the password after five wrong codes", async () => {
    const { user, signIn } = await passwordForm();
    await signIn("owner@farm.test");
    await screen.findByRole("heading", { name: "Two-factor code" });
    for (let i = 0; i < 5; i++) {
      await user.clear(screen.getByLabelText("Code"));
      await user.type(screen.getByLabelText("Code"), "000000");
      await user.click(screen.getByRole("button", { name: "Continue" }));
      await screen.findByRole("alert");
    }
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Too many wrong codes: sign in again");
  });

  it("sets two-factor up at an Owner's first sign-in", async () => {
    const { user, signIn } = await passwordForm();
    await signIn("new-owner@farm.test");

    await screen.findByRole("heading", { name: "Set up two-factor" });
    await user.click(screen.getByRole("button", { name: "Show the key" }));
    expect(await screen.findByLabelText("Authenticator key")).toHaveTextContent(/^[A-Z2-7]+$/);
    expect(screen.getByRole("link", { name: "open it in the app" })).toHaveAttribute(
      "href",
      expect.stringMatching(/^otpauth:\/\/totp\//),
    );
    await user.type(screen.getByLabelText("Code"), FAKE_CODE);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
  });
});

describe("Demo sign-in", () => {
  it("signs in as the picked role's demo user through POST /api/auth/demo", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    fakeSignIn(null);
    const user = userEvent.setup();
    renderFresh("/");
    await user.click(await screen.findByRole("button", { name: /^Operator/ }));
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(topBar().getByText("Demo Operator")).toBeInTheDocument();
  });
});

describe("The session", () => {
  it("survives a reload: GET /api/me says who is signed in", async () => {
    fakeSignIn("AGRONOMIST");
    renderFresh("/climate");
    expect(await screen.findByRole("heading", { name: "Climate" })).toBeInTheDocument();
    expect(topBar().getByText(/· Agronomist/)).toBeInTheDocument();
  });

  it("goes to the sign-in page once the server says it has ended (401)", async () => {
    const user = userEvent.setup();
    renderAt("/zone/Z1", { role: "Operator" });
    const card = within((await screen.findByRole("heading", { name: "Manual dose" })).closest("section")!);
    resetSession(); // expired, or signed out on another device
    await user.type(card.getByLabelText("Stock A"), "120");
    await user.click(card.getByRole("button", { name: "Review dose" }));
    expect(await screen.findByRole("heading", { name: /^Sign in/ })).toBeInTheDocument();
  });
});
