import { NavLink } from "react-router-dom";
import { Icon } from "../components/Icon";
import { NAV_MONITOR, NAV_SYSTEM, type NavItem } from "./nav";
import { SITE_NAME } from "./site";

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
        <span>{SITE_NAME}</span>
      </div>
      <Group title="Monitor" items={NAV_MONITOR} onNavigate={onNavigate} />
      <Group title="System" items={NAV_SYSTEM} onNavigate={onNavigate} />
    </nav>
  );
}
