import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { makeQueryClient } from "../app/queryClient";
import { routes } from "../app/routes";
import { toServerRole, type Role } from "../auth/roles";
import { SessionProvider } from "../auth/SessionProvider";
import { fakeSignIn, fakeSignInAccount } from "../mocks/session";

/** Render the whole app at a path, backed by the fake API, signed in as `role`'s demo user
    (null = signed out), or as an `account` with a password by its ID ("u-agronomist"). The fake
    API's session is the same one, so its role checks apply. Pass `queryClient` to refresh a query
    yourself instead of waiting for its poll. */
export function renderAt(
  path: string,
  {
    role = "Owner",
    account,
    queryClient = makeQueryClient({ retry: false }),
  }: { role?: Role | null; account?: string; queryClient?: QueryClient } = {},
) {
  const me = account ? fakeSignInAccount(account) : fakeSignIn(role ? toServerRole(role) : null);
  return render(
    <QueryClientProvider client={queryClient}>
      <SessionProvider initialMe={me}>
        <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />
      </SessionProvider>
    </QueryClientProvider>,
  );
}
