import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";
import { navGroups } from "../nav";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { getOverviewReport } from "../lib/api";
import { AiTaskQueueChip } from "./AiTaskQueueChip";
import { NavIcon } from "./NavIcon";
import { designwahl } from "../styles/design";

interface SidebarProps {
  onNavigate?: () => void;
  onOpenAiTasks: () => void;
  /** Oeffnet/schliesst das KI-Feld; fehlt ohne Anmeldung oder Haushalt. */
  onToggleAssistant?: () => void;
  assistantOpen?: boolean;
}

export function Sidebar({
  onNavigate,
  onOpenAiTasks,
  onToggleAssistant,
  assistantOpen = false,
}: SidebarProps) {
  const { session, signOut } = useAuth();
  const { households, selectedHouseholdId, selectHousehold } = useHousehold();
  const [filterQuery, setFilterQuery] = useState("");

  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  const { data: overviewReport } = useQuery({
    queryKey: ["overview-report", householdId],
    queryFn: () => getOverviewReport(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const openReviewCount = overviewReport?.open_review_count ?? 0;

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFilterQuery(e.target.value.toLowerCase());
  };

  return (
    <aside className="sidebar" data-testid="app-sidebar">
      {/* Brand & Theme Header */}
      <div className="sidebar-brand">
        <div className="sidebar-brand-title">
          <span className="sidebar-brand-name">Finanzplaner</span>
          <span className="sidebar-brand-tag">PRO</span>
        </div>
        {/* ◐ oeffnet das Designfenster mit allen Designs (public/designkasten/
            designwahl.js); aria-expanded fuehrt das Fenster selbst nach. */}
        <button
          type="button"
          className="design-toggle"
          onClick={(event) => designwahl()?.umschalten(event.currentTarget)}
          disabled={designwahl() === null}
          aria-label="Design wählen"
          aria-haspopup="menu"
          aria-expanded="false"
          title="Design wählen"
          data-testid="design-toggle"
        >
          <span aria-hidden="true">◐</span>
        </button>
      </div>

      {/* Household Selector Card */}
      {session && households.length > 0 && (
        <div className="sidebar-household">
          {households.length > 1 ? (
            <select
              className="household-select"
              value={selectedHouseholdId ?? ""}
              onChange={(event) => selectHousehold(event.target.value)}
              aria-label="Haushalt wechseln"
              data-testid="household-select"
            >
              {households.map((household) => (
                <option key={household.household_id} value={household.household_id}>
                  {household.name}
                </option>
              ))}
            </select>
          ) : (
            <div className="household-single">
              <span className="household-avatar">{households[0]?.name?.charAt(0) ?? "H"}</span>
              <span className="household-name">{households[0]?.name}</span>
            </div>
          )}
        </div>
      )}

      {/* Quick Search / Filter Input */}
      <div className="sidebar-search">
        <NavIcon name="search" className="sidebar-search-icon" />
        <input
          type="text"
          className="sidebar-search-input"
          placeholder="Filter..."
          value={filterQuery}
          onChange={handleSearchChange}
          aria-label="Navigation filtern"
        />
        {filterQuery && (
          <button
            type="button"
            className="sidebar-search-clear"
            onClick={() => setFilterQuery("")}
            aria-label="Filter zurücksetzen"
          >
            ×
          </button>
        )}
      </div>

      {/* Main Navigation */}
      <nav className="sidebar-nav" aria-label="Hauptnavigation">
        {navGroups.map((group) => {
          const headingId = `sidebar-nav-group-${group.id}`;
          const filteredItems = group.items.filter((item) =>
            item.label.toLowerCase().includes(filterQuery),
          );

          if (filterQuery && filteredItems.length === 0) {
            return null;
          }

          return (
            <section className="sidebar-nav-group" key={group.id}>
              <h2 className="sidebar-nav-group-title" id={headingId}>
                <span className="sidebar-nav-group-code" aria-hidden="true">
                  {group.code}
                </span>
                <span>{group.label}</span>
              </h2>
              <ul aria-labelledby={headingId}>
                {filteredItems.map((item) => (
                  <li key={item.path}>
                    <NavLink
                      to={item.path}
                      end={item.path === "/"}
                      onClick={onNavigate}
                      data-testid={`nav-${
                        item.path === "/" ? "home" : item.path.slice(1).replaceAll("/", "-")
                      }`}
                      className={({ isActive }) =>
                        isActive ? "sidebar-link sidebar-link--active" : "sidebar-link"
                      }
                    >
                      <span className="sidebar-link-content">
                        <NavIcon name={item.icon} className="sidebar-link-icon" />
                        <span>{item.label}</span>
                      </span>
                      {item.path === "/review" && openReviewCount > 0 && (
                        <span className="status-badge status-badge--warning sidebar-link-badge">
                          {openReviewCount}
                        </span>
                      )}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </nav>

      {/* Footer Telemetry & User Session */}
      <div className="sidebar-footer">
        {onToggleAssistant && (
          <button
            type="button"
            className={`assistant-open-btn${assistantOpen ? " assistant-open-btn--active" : ""}`}
            aria-expanded={assistantOpen}
            onClick={onToggleAssistant}
            title="KI-Feld: Fragen zu deinen Finanzen, von jeder Seite aus"
            data-testid="assistant-toggle"
          >
            <NavIcon name="message-circle" className="assistant-open-btn__icon" />
            <span>KI fragen</span>
          </button>
        )}
        <AiTaskQueueChip onOpen={onOpenAiTasks} />

        {session && (
          <button type="button" className="sidebar-logout" onClick={() => void signOut()}>
            Abmelden
          </button>
        )}
      </div>
    </aside>
  );
}
