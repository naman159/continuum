import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { useChapterCap } from "../hooks/useChapterCap";
import { api } from "../api";
import type { ReactNode } from "react";

function Icon({ path, path2 }: { path: string; path2?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
      {path2 && <path d={path2} />}
    </svg>
  );
}

const NAV_ICONS: Record<string, ReactNode> = {
  Characters: <Icon path="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" path2="M9 7a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm13 14v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />,
  Chapters: <Icon path="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" path2="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />,
  Scenes: <Icon path="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 0 0 1-1V5a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1z" />,
  Timeline: <Icon path="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z" path2="M12 6v6l4 2" />,
  Threads: <Icon path="M12 2 2 7l10 5 10-5-10-5z" path2="M2 17l10 5 10-5M2 12l10 5 10-5" />,
  Commitments: <Icon path="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />,
  Locations: <Icon path="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" path2="M12 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />,
  Objects: <Icon path="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" path2="M3.27 6.96 12 12.01l8.73-5.05M12 22.08V12" />,
  Factions: <Icon path="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />,
  "State & Knowledge": <Icon path="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" path2="M12 17h.01" />,
  "Canon Facts": <Icon path="M22 11.08V12a10 10 0 1 1-5.93-9.14" path2="M22 4 12 14.01l-3-3" />,
  Dynamics: <Icon path="M22 12h-4l-3 9L9 3l-3 9H2" />,
  "Entity Graph": <Icon path="M8.59 13.51l6.83 3.98M15.41 6.51l-6.82 3.98" path2="M21 5a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM9 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM21 19a3 3 0 1 1-6 0 3 3 0 0 1 6 0z" />,
  Continuity: <Icon path="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" path2="M9 12l2 2 4-4" />,
  "Process Chapter": <Icon path="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />,
};

function navIcon(label: string): ReactNode {
  return NAV_ICONS[label] ?? (
    <Icon path="M4 6h16M4 12h16M4 18h16" />
  );
}

type NavGroup = { label: string; links: [string, string][] };

function buildNavGroups(novelId: string, customLinks: [string, string][]): NavGroup[] {
  const groups: NavGroup[] = [
    {
      label: "Story",
      links: [
        ["Characters", `/novels/${novelId}/characters`],
        ["Chapters", `/novels/${novelId}/chapters`],
        ["Scenes", `/novels/${novelId}/scenes`],
      ],
    },
    {
      label: "Narrative",
      links: [
        ["Timeline", `/novels/${novelId}/timeline`],
        ["Threads", `/novels/${novelId}/threads`],
        ["Commitments", `/novels/${novelId}/commitments`],
      ],
    },
    {
      label: "World",
      links: [
        ["Locations", `/novels/${novelId}/locations`],
        ["Objects", `/novels/${novelId}/objects`],
        ["Factions", `/novels/${novelId}/factions`],
      ],
    },
    {
      label: "Analysis",
      links: [
        ["State & Knowledge", `/novels/${novelId}/knowledge`],
        ["Canon Facts", `/novels/${novelId}/canon`],
        ["Dynamics", `/novels/${novelId}/dynamics`],
        ["Entity Graph", `/novels/${novelId}/entity-graph`],
        ["Continuity", `/novels/${novelId}/continuity`],
        ["Relationships", `/novels/${novelId}/relationships`],
      ],
    },
  ];

  if (customLinks.length > 0) {
    groups.push({ label: "Custom", links: customLinks });
  }

  groups.push({
    label: "Tools",
    links: [["Process Chapter", `/novels/${novelId}/process`]],
  });

  return groups;
}

export default function Sidebar() {
  const { novelId } = useParams();
  const location = useLocation();
  const [cap, setCap] = useChapterCap();

  const novelQuery = useQuery({
    queryKey: ["novel", novelId],
    queryFn: () => api.novel(novelId!),
    enabled: Boolean(novelId),
  });

  const entityTypesQuery = useQuery({
    queryKey: ["entity-types", novelId],
    queryFn: () => api.entityTypes(novelId!),
    enabled: Boolean(novelId),
  });

  const max = novelQuery.data?.max_chapter ?? null;
  const effective = cap ?? max ?? 0;

  const customLinks: [string, string][] = novelId
    ? (entityTypesQuery.data ?? []).map((et) => {
        const label = et.name.replace(/_/g, " ");
        const capitalized = label.charAt(0).toUpperCase() + label.slice(1);
        const displayLabel = capitalized.endsWith("s") ? capitalized : capitalized + "s";
        return [displayLabel, `/novels/${novelId}/entity-types/${et.name}/entities`];
      })
    : [];

  const navGroups = novelId ? buildNavGroups(novelId, customLinks) : [];

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <Link to="/novels">
          <span className="sidebar-logo-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14 }}>
              <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
              <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
            </svg>
          </span>
          Continuum
        </Link>
      </div>

      {novelQuery.data && (
        <div className="sidebar-novel">
          <span className="sidebar-novel-label">Novel</span>
          <strong className="sidebar-novel-title">{novelQuery.data.title}</strong>
        </div>
      )}

      <nav>
        {navGroups.map((group) => (
          <div className="nav-section" key={group.label}>
            <span className="nav-section-label">{group.label}</span>
            <ul className="nav-items">
              {group.links.map(([label, to]) => (
                <li key={to}>
                  <Link
                    to={`${to}${location.search}`}
                    className={location.pathname.startsWith(to) ? "active" : ""}
                  >
                    {navIcon(label)}
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}

        {!novelId && (
          <div className="nav-section">
            <ul className="nav-items">
              <li>
                <Link to="/novels" className={location.pathname === "/novels" ? "active" : ""}>
                  <Icon path="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" path2="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
                  Library
                </Link>
              </li>
            </ul>
          </div>
        )}
      </nav>

      {max != null && (
        <div className="chapter-cap">
          <div className="chapter-cap-row">
            <span className="chapter-cap-label">Chapter cap</span>
            <span className="chapter-cap-value">ch {effective}</span>
          </div>
          <input
            type="range"
            min={1}
            max={max}
            value={effective}
            onChange={(e) => setCap(Number(e.target.value))}
            aria-label="Chapter cap"
          />
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              className="chapter-cap-reset"
              onClick={() => setCap(null)}
              aria-label="Show all chapters"
            >
              Show all
            </button>
          </div>
        </div>
      )}
    </aside>
  );
}
