"use client";

import Link from "next/link";
import type { WorkspaceSection } from "@/lib/types";
import { navItems } from "@/lib/data";

type SidebarProps = {
  activeSection: WorkspaceSection;
  actionTone: string;
  ibConnected?: boolean;
  lastSync?: string | null;
};

export default function Sidebar({ activeSection, actionTone, ibConnected = true, lastSync }: SidebarProps) {
  const syncTime = lastSync ? new Date(lastSync).toLocaleTimeString() : "—";

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="logo-icon" />
        <span className="logo-text">Radon</span>
      </div>

      <nav className="sidebar-nav">
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <Link
              key={item.label}
              href={item.href}
              className={item.route === activeSection ? "nav-item active" : "nav-item"}
            >
              <span className="nav-icon">
                <Icon size={14} color={actionTone} strokeWidth={2} />
              </span>
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="sidebar-footer">
        <div className="status-row">
          <span>IB Gateway</span>
          <span className="status-dot-wrap">
            <span className={`status-dot ${ibConnected ? "status-dot-live" : "status-dot-dead"}`} />
            {ibConnected ? "CONNECTED" : "OFFLINE"}
          </span>
        </div>
        <div className="status-row">
          <span>Last Sync</span>
          <span>{syncTime}</span>
        </div>
        <div className="status-row">
          <span>Port</span>
          <span>4001</span>
        </div>
      </div>
    </aside>
  );
}
