import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { makeQueryClient } from "../app/queryClient";
import { routes } from "../app/routes";
import { toServerRole, type Role } from "../auth/roles";
import { SessionProvider } from "../auth/SessionProvider";
import { fakeSignIn } from "../mocks/session";

/** Render the whole app at a path, backed by the fake API, signed in as `role`'s demo user
    (null = signed out). The fake API's session is the same one, so its role checks apply. */
export function renderAt(path: string, { role = "Owner" }: { role?: Role | null } = {}) {
  const me = fakeSignIn(role ? toServerRole(role) : null);
  return render(
    <QueryClientProvider client={makeQueryClient({ retry: false })}>
      <SessionProvider initialMe={me}>
        <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />
      </SessionProvider>
    </QueryClientProvider>,
  );
}
