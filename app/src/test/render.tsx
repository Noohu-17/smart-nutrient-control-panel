import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { makeQueryClient } from "../app/queryClient";
import { routes } from "../app/routes";
import type { Role } from "../auth/roles";
import { SessionProvider } from "../auth/SessionProvider";

/** Render the whole app at a path, backed by the fake API, signed in as `role` (null = signed out). */
export function renderAt(path: string, { role = "Owner" }: { role?: Role | null } = {}) {
  return render(
    <SessionProvider initialRole={role}>
      <QueryClientProvider client={makeQueryClient({ retry: false })}>
        <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />
      </QueryClientProvider>
    </SessionProvider>,
  );
}
