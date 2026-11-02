import type { RouteObject } from "react-router-dom";
import { Alerts } from "../pages/Alerts";
import { Climate } from "../pages/Climate";
import { History } from "../pages/History";
import { NotFound } from "../pages/NotFound";
import { Overview } from "../pages/Overview";
import { SignIn } from "../pages/SignIn";
import { SimulatorPage } from "../pages/SimulatorPage";
import { ZonePage } from "../pages/ZonePage";
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
