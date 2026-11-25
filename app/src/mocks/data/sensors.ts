// src/mocks/data/sensors.ts · the fake GET /api/sensors answer (DM-3)
// sensors.json is a copy of config/clients/client_A/sensors.json; handlers.test.ts fails if they drift apart.
import type { SensorCatalogue } from "../../api/types";
import sensorsJson from "./sensors.json";

// §11.2: the pack's file without its _meta block
export const sensorCatalogue: SensorCatalogue = {
  v: sensorsJson.v,
  growingSystems: sensorsJson.growingSystems,
  farmTypes: sensorsJson.farmTypes as SensorCatalogue["farmTypes"],
};
