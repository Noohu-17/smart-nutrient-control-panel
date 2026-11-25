import { useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useFarm } from "../api/hooks";
import { useSession } from "../auth/session";
import { DemoBanner } from "../components/DemoBanner";
import { SimBanner } from "../components/SimBanner";
import { SiteAlerts } from "../features/alerts/SiteAlerts";
import { AlertToasts } from "../features/notifications/AlertToasts";
import { usePushClicks } from "../features/notifications/usePushClicks";
import { SideBySide } from "../features/simulator/SideBySide";
import { BottomTabs } from "./BottomTabs";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

const SIDE_BY_SIDE_KEY = "snm.sideBySide";

/** Whether the Simulator shows beside the page, remembered in this browser. */
function useSideBySide(): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(() => {
    try {
      return localStorage.getItem(SIDE_BY_SIDE_KEY) === "on";
    } catch {
      return false;
    }
  });
  const set = (next: boolean) => {
    setOn(next);
    try {
      if (next) localStorage.setItem(SIDE_BY_SIDE_KEY, "on");
      else localStorage.removeItem(SIDE_BY_SIDE_KEY);
    } catch {
      /* storage blocked: the choice still applies until the page reloads */
    }
  };
  return [on, set];
}

/** Pages that show alerts their own way; every other page gets the site-wide strip. */
const hasOwnAlerts = (path: string) => path === "/" || path.startsWith("/zone/") || path === "/alerts";

/** Top bar · sidebar on desktop · bottom tabs + menu sheet on phone. Signed-in people only. */
export function Layout() {
  const { role, checking } = useSession();
  const { pathname } = useLocation();
  usePushClicks();
  // a reload: wait for GET /api/me rather than send a signed-in person to the sign-in page
  if (checking)
    return (
      <main className="signin">
        <p role="status">Checking sign-in…</p>
      </main>
    );
  if (!role) return <Navigate to="/sign-in" replace state={{ from: pathname }} />;
  return <Shell pathname={pathname} />;
}

/** The signed-in screen; on a playground site, optionally with the Simulator beside the page. */
function Shell({ pathname }: { pathname: string }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const playground = useFarm().data?.features.playground === true;
  const [sideBySide, setSideBySide] = useSideBySide();
  // the Simulator itself needs no second copy beside it
  const split = playground && sideBySide && pathname !== "/simulator";
  return (
    <>
      <DemoBanner />
      <SimBanner />
      <TopBar
        sideBySide={playground ? { on: sideBySide, toggle: () => setSideBySide(!sideBySide) } : undefined}
      />
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
          {split ? (
            <SideBySide>
              <div className="wrap">
                {!hasOwnAlerts(pathname) && <SiteAlerts />}
                <Outlet />
              </div>
            </SideBySide>
          ) : (
            <div className="wrap">
              {!hasOwnAlerts(pathname) && <SiteAlerts />}
              <Outlet />
            </div>
          )}
        </main>
      </div>
      <AlertToasts />
      <BottomTabs moreOpen={menuOpen} onMore={() => setMenuOpen((o) => !o)} />
    </>
  );
}
