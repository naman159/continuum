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

  const max = novelQuery.data?.max_chapter ?? null;
  const effective = cap ?? max ?? 0;

  const links = novelId
    ? [
        ["Characters", `/novels/${novelId}/characters`],
        ["Chapters", `/novels/${novelId}/chapters`],
        ["Timeline", `/novels/${novelId}/timeline`],
        ["Threads", `/novels/${novelId}/threads`],
        ["Relationships", `/novels/${novelId}/relationships`],
        ["Continuity", `/novels/${novelId}/continuity`],
      ]
    : [];

  return (
    <aside className="sidebar">
      <h1>
        <Link to="/novels">Continuum</Link>
      </h1>
      {novelQuery.data && <h2>{novelQuery.data.title}</h2>}
      <nav>
        <ul>
          {links.map(([label, to]) => (
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
