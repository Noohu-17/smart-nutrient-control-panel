// src/mocks/data/layout.ts · the fake GET /api/farm answer (contract §2.4)
// layout.json is a copy of config/clients/client_A/layout.json; layout.test.ts fails if they drift apart.
// features.json is a copy of config/clients/client_A/features.json, checked the same way.
import type { Farm, Features } from "../../api/types";
import featuresJson from "./features.json";
import layoutJson from "./layout.json";

export const farmFeatures: Features = featuresJson;
export const farmLayout: Farm = { ...(layoutJson as Omit<Farm, "features">), features: farmFeatures };
