import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Factions() {
  const { novelId } = useParams();
  const location = useLocation();
  const [cap] = useChapterCap();
  const { data, isLoading, error } = useQuery({
    queryKey: ["factions", novelId, cap],
    queryFn: () => api.factions(novelId!, cap),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data || data.length === 0) return <p>No factions.</p>;
  return (
    <div>
      <h1>Factions</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Aliases</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          {data.map((f) => (
            <tr key={f.id}>
              <td>
                <Link to={`/novels/${novelId}/factions/${f.id}${location.search}`}>{f.name}</Link>
              </td>
              <td>{f.aliases.join(", ") || "—"}</td>
              <td>{f.description ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
