// T2-4 done-when: the Overview shows Zone A and Zone B from fake data
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { Overview } from "./Overview";

// the fake API waits 300 ms per call, so allow more than the 1 s default
const WAIT = { timeout: 3000 };

describe("Overview on the fake API", () => {
  it("shows both zone names that came from /api/farm", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Overview />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "Zone A" }, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Zone B" })).toBeInTheDocument();
  });
});
