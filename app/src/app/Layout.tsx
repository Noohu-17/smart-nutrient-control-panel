import { useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useSession } from "../auth/session";
import { DemoBanner } from "../components/DemoBanner";
import { SimBanner } from "../components/SimBanner";
import { SiteAlerts } from "../features/alerts/SiteAlerts";
import { BottomTabs } from "./BottomTabs";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/** Pages that show alerts their own way; every other page gets the site-wide strip. */
const hasOwnAlerts = (path: string) => path === "/" || path.startsWith("/zone/") || path === "/alerts";

/** Top bar · sidebar on desktop · bottom tabs + menu sheet on phone. Signed-in people only. */
export function Layout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { role } = useSession();
  const { pathname } = useLocation();
  if (!role) return <Navigate to="/sign-in" replace state={{ from: pathname }} />;
  return (
    <>
      <DemoBanner />
      <SimBanner />
      <TopBar />
      <div className="shell">
        <Sidebar open={menuOpen} onNavigate={() => setMenuOpen(false)} />
        <button
          type="button"
          className={`menu-backdrop${menuOpen ? " open" : ""}`}
          aria-label="Close menu"
          tabIndex={menuOpen ? 0 : -1}
          onClick={() => setMenuOpen(false)}
        />
        <main className="page" id="main">
          <div className="wrap">
            {!hasOwnAlerts(pathname) && <SiteAlerts />}
            <Outlet />
          </div>
        </main>
      </div>
      <BottomTabs moreOpen={menuOpen} onMore={() => setMenuOpen((o) => !o)} />
    </>
  );
}
