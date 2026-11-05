import type { RouteObject } from "react-router-dom";
import { NotFound } from "../components/NotFound";
import { Alerts } from "../features/alerts/Alerts";
import { SignIn } from "../features/auth/SignIn";
import { Climate } from "../features/climate/Climate";
import { History } from "../features/history/History";
import { Overview } from "../features/overview/Overview";
import { SimulatorPage } from "../features/simulator/SimulatorPage";
import { ZonePage } from "../features/zones/ZonePage";
import { Layout } from "./Layout";

export const routes: RouteObject[] = [
  { path: "/sign-in", element: <SignIn /> },
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Overview /> },
      { path: "zone/:zoneId", element: <ZonePage /> },
      { path: "climate", element: <Climate /> },
      { path: "alerts", element: <Alerts /> },
      { path: "history", element: <History /> },
      { path: "simulator", element: <SimulatorPage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
];
