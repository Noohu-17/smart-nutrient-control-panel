import { QueryClient } from "@tanstack/react-query";

/** One client for the app; tests pass retry: false so failures show at once. */
export function makeQueryClient({ retry = 1 }: { retry?: number | false } = {}) {
  return new QueryClient({ defaultOptions: { queries: { retry, refetchOnWindowFocus: false } } });
}
