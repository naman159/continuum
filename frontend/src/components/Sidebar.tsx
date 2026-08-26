import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { useChapterCap } from "../hooks/useChapterCap";
import { api } from "../api";
import type { ReactNode } from "react";
import {
  Users,
  BookOpen,
  Film,
  Clock,
  Layers,
  Bookmark,
  MapPin,
  Package,
  Shield,
  HelpCircle,
  CheckCircle,
  Activity,
  Share2,
  ShieldCheck,
  Zap,
  Menu,
  Search,
} from "lucide-react";

const NAV_ICONS: Record<string, ReactNode> = {
  Search: <Search />,
  Characters: <Users />,
  Chapters: <BookOpen />,
  Scenes: <Film />,
  Timeline: <Clock />,
  Threads: <Layers />,
  Commitments: <Bookmark />,
  Locations: <MapPin />,
  Objects: <Package />,
  Factions: <Shield />,
  "State & Knowledge": <HelpCircle />,
  "Canon Facts": <CheckCircle />,
  Dynamics: <Activity />,
  "Entity Graph": <Share2 />,
  Continuity: <ShieldCheck />,
  "Process Chapter": <Zap />,
  "Review Queue": <Zap />,
};

function navIcon(label: string): ReactNode {
  return NAV_ICONS[label] ?? <Menu />;
}

type NavGroup = { label: string; links: [string, string][] };

function buildNavGroups(novelId: string, customLinks: [string, string][]): NavGroup[] {
  const groups: NavGroup[] = [
    {
      label: "Story",
      links: [
        ["Search", `/novels/${novelId}/search`],
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
    links: [
      ["Process Chapter", `/novels/${novelId}/process`],
      ["Review Queue", `/novels/${novelId}/review`],
    ],
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
            <BookOpen />
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
                  <BookOpen />
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
