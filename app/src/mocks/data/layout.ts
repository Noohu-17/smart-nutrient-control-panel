// src/mocks/data/layout.ts · the fake GET /api/farm answer (contract §2.4)
// layout.json is a copy of config/clients/client_A/layout.json; layout.test.ts fails if they drift apart.
import type { Farm } from "../../api/types";
import layoutJson from "./layout.json";

export const farmLayout = layoutJson as Farm;
