import { NavLink, useLocation } from "react-router-dom";
import { useFarm } from "../api/hooks";
import { Icon } from "../components/Icon";
import { zonePath } from "../config/readings";

/** Phone navigation: four tabs + More (opens the full menu). */
export function BottomTabs({ onMore, moreOpen }: { onMore: () => void; moreOpen: boolean }) {
  const { pathname } = useLocation();
  const firstZone = useFarm().data?.zones[0];
  return (
    <nav className="ptabs" aria-label="Tabs">
      <NavLink to="/" end>
        <Icon name="grid" />
        Home
      </NavLink>
      <NavLink
        to={firstZone ? zonePath(firstZone.id) : "/"}
        className={pathname.startsWith("/zone/") ? "active" : undefined}
      >
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
