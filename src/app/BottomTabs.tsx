import { NavLink, useLocation } from "react-router-dom";
import { Icon } from "../components/Icon";
import { ZONES, zonePath } from "./nav";

/** Phone navigation: four tabs + More (opens the full menu). */
export function BottomTabs({ onMore, moreOpen }: { onMore: () => void; moreOpen: boolean }) {
  const { pathname } = useLocation();
  return (
    <nav className="ptabs" aria-label="Tabs">
      <NavLink to="/" end>
        <Icon name="grid" />
        Home
      </NavLink>
      <NavLink to={zonePath(ZONES[0].id)} className={pathname.startsWith("/zone/") ? "active" : undefined}>
        <Icon name="layers" />
        Zones
      </NavLink>
      <NavLink to="/climate">
        <Icon name="therm" />
        Climate
      </NavLink>
      <NavLink to="/alerts">
        <Icon name="bell" />
        Alerts
      </NavLink>
      <button type="button" onClick={onMore} aria-expanded={moreOpen}>
        <Icon name="menu" />
        More
      </button>
    </nav>
  );
}
