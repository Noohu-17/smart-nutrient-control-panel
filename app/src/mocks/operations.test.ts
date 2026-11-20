// src/mocks/operations.test.ts · T2-23 · the fake GET /api/operations pages like the real one (T1-28)
import { describe, expect, it } from "vitest";
import type { OperationList } from "../api/types";
import { listOperations, recordOperation } from "./operations";

const BY = { id: "u1", name: "Ravi Operator", role: "OPERATOR" as const };
const stop = (cmdId: string, at: number, zone: string | null = null) =>
  recordOperation(
    {
      kind: "STOP",
      by: BY,
      zone,
      target: zone ? { scope: "ZONE", zone } : { scope: "ALL_OPERATIONS" },
      cmdId,
      alert: null,
    },
    at,
  );
const ids = (query: Record<string, string>) =>
  listOperations(new URLSearchParams(query))!.items.map((op) => op.cmdId);

describe("fake GET /api/operations", () => {
  it("answers newest first, a page at a time, each action once even at the same moment", () => {
    stop("a", 1000);
    stop("b", 2000);
    stop("c", 2000);
    stop("d", 3000);
    const seen: (string | null)[] = [];
    let before: string | null = null;
    do {
      const page: OperationList = listOperations(
        new URLSearchParams({ limit: "1", ...(before ? { before } : {}) }),
      )!;
      seen.push(...page.items.map((op) => op.cmdId));
      before = page.next;
    } while (before);
    expect(seen).toEqual(["d", "c", "b", "a"]);
  });

  it("keeps a zone's actions and the site-wide stops, and one kind", () => {
    stop("z2", 1000, "Z2");
    stop("all", 2000);
    recordOperation(
      { kind: "RESUME", by: BY, zone: "Z1", target: { zone: "Z1" }, cmdId: "r1", alert: null },
      3000,
    );
    expect(ids({ zone: "Z1" })).toEqual(["r1", "all"]);
    expect(ids({ kind: "STOP" })).toEqual(["all", "z2"]);
    expect(ids({ before: new Date(2500).toISOString() })).toEqual(["all", "z2"]); // a plain time
  });

  it("refuses a query the real one refuses (422)", () => {
    const refused: Record<string, string>[] = [
      { kind: "REFILL" },
      { limit: "0" },
      { limit: "501" },
      { before: "yesterday" },
    ];
    for (const query of refused) expect(listOperations(new URLSearchParams(query))).toBeNull();
  });
});
