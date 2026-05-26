import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { useChapterCap } from "../hooks/useChapterCap";
import { api } from "../api";

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

  const staticLinks: [string, string][] = novelId
    ? [
        ["Characters", `/novels/${novelId}/characters`],
        ["Chapters", `/novels/${novelId}/chapters`],
        ["Scenes", `/novels/${novelId}/scenes`],
        ["Timeline", `/novels/${novelId}/timeline`],
        ["Threads", `/novels/${novelId}/threads`],
        ["Commitments", `/novels/${novelId}/commitments`],
        ["State & Knowledge", `/novels/${novelId}/knowledge`],
        ["Canon Facts", `/novels/${novelId}/canon`],
        ["Locations", `/novels/${novelId}/locations`],
        ["Objects", `/novels/${novelId}/objects`],
        ["Factions", `/novels/${novelId}/factions`],
        ["Dynamics", `/novels/${novelId}/dynamics`],
        ["Continuity", `/novels/${novelId}/continuity`],
        ["Process Chapter", `/novels/${novelId}/process`],
      ]
    : [];

  const customLinks: [string, string][] = (entityTypesQuery.data ?? []).map((et) => {
    const label = et.name.replace(/_/g, " ");
    const displayLabel = label.charAt(0).toUpperCase() + label.slice(1) + "s";
    return [displayLabel, `/novels/${novelId}/entity-types/${et.name}/entities`];
  });

  const allLinks = [...staticLinks, ...customLinks];

  return (
    <aside className="sidebar">
      <h1>
        <Link to="/novels">Continuum</Link>
      </h1>
      {novelQuery.data && <h2>{novelQuery.data.title}</h2>}
      <nav>
        <ul>
          {allLinks.map(([label, to]) => (
            <li key={to}>
              <Link
                to={`${to}${location.search}`}
                className={location.pathname.startsWith(to) ? "active" : ""}
              >
                {label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      {max != null && (
        <div className="chapter-cap">
          <label htmlFor="cap-slider">As of chapter {effective}</label>
          <input
            id="cap-slider"
            type="range"
            min={1}
            max={max}
            value={effective}
            onChange={(e) => setCap(Number(e.target.value))}
          />
          <button onClick={() => setCap(null)}>Show all</button>
        </div>
      )}
    </aside>
  );
}
