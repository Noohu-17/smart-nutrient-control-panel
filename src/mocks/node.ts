// src/mocks/node.ts · the same fake API for tests (Vitest runs in Node, not in a browser)
import { setupServer } from "msw/node";
import { handlers } from "./handlers";

export const server = setupServer(...handlers);
