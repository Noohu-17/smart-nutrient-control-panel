import { useState } from "react";
import { Outlet } from "react-router-dom";
import { BottomTabs } from "./BottomTabs";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/** Top bar · sidebar on desktop · bottom tabs + menu sheet on phone. */
export function Layout() {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <>
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
            <Outlet />
          </div>
        </main>
      </div>
      <BottomTabs moreOpen={menuOpen} onMore={() => setMenuOpen((o) => !o)} />
    </>
  );
}
