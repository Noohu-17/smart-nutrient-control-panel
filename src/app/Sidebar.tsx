import { NavLink } from "react-router-dom";
import { useFarm } from "../api/hooks";
import { Icon } from "../components/Icon";
import { navMonitor, NAV_SYSTEM, type NavItem } from "./nav";

function Group({ title, items, onNavigate }: { title: string; items: NavItem[]; onNavigate: () => void }) {
  return (
    <div className="navgrp">
      <div>{title}</div>
      {items.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.to === "/"} onClick={onNavigate}>
          <Icon name={item.icon} />
          {item.label}
        </NavLink>
      ))}
    </div>
  );
}

export function Sidebar({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const farm = useFarm();
  return (
    <nav className={`side${open ? " open" : ""}`} aria-label="Main">
      <div className="brand">
        <div className="logo">
          <Icon name="drop" size={24} filled />
        </div>
        <div>
          <b>
            Smart Nutrient
            <br />
            Advisor
          </b>
          <span>Control portal · Phase 2A</span>
        </div>
      </div>
      <div className="site-pick">
        <small>Site</small>
        <span>{farm.data?.name ?? "…"}</span>
      </div>
      <Group title="Monitor" items={navMonitor(farm.data?.zones ?? [])} onNavigate={onNavigate} />
      <Group title="System" items={NAV_SYSTEM} onNavigate={onNavigate} />
    </nav>
  );
}
