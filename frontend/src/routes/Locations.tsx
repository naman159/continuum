import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Locations() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const location = useLocation();
  const { data, isLoading, error } = useQuery({
    queryKey: ["locations", novelId, cap],
    queryFn: () => api.locations(novelId!, cap),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data || data.length === 0) return <p>No locations.</p>;
  return (
    <div>
      <h1>Locations</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Aliases</th>
            <th>Description</th>
            <th>First seen</th>
          </tr>
        </thead>
        <tbody>
          {data.map((l) => (
            <tr key={l.id}>
              <td>
                <Link to={`/novels/${novelId}/locations/${l.id}${location.search}`}>{l.name}</Link>
              </td>
              <td>{l.aliases.join(", ") || "—"}</td>
              <td>{l.description ?? "—"}</td>
              <td>{l.first_appearance_chapter ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
