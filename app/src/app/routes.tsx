import type { RouteObject } from "react-router-dom";
import { Overview } from "../pages/Overview";
import { Placeholder } from "../pages/Placeholder";
import { SimulatorPage } from "../pages/SimulatorPage";
import { ZonePage } from "../pages/ZonePage";
import { Layout } from "./Layout";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Overview /> },
      { path: "zone/:zoneId", element: <ZonePage /> },
      { path: "climate", element: <Placeholder title="Climate" /> },
      { path: "alerts", element: <Placeholder title="Alerts" /> },
      { path: "history", element: <Placeholder title="History" /> },
      { path: "simulator", element: <SimulatorPage /> },
      { path: "*", element: <Placeholder title="Page not found" note="This page does not exist." /> },
    ],
  },
];
