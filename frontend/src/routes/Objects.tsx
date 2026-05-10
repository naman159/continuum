import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Objects() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const location = useLocation();
  const { data, isLoading, error } = useQuery({
    queryKey: ["objects", novelId, cap],
    queryFn: () => api.objects(novelId!, cap),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data || data.length === 0) return <p>No objects.</p>;
  return (
    <div>
      <h1>Objects</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Aliases</th>
            <th>Significance</th>
            <th>Description</th>
            <th>First seen</th>
          </tr>
        </thead>
        <tbody>
          {data.map((o) => (
            <tr key={o.id}>
              <td>
                <Link to={`/novels/${novelId}/objects/${o.id}${location.search}`}>{o.name}</Link>
              </td>
              <td>{o.aliases.join(", ") || "—"}</td>
              <td>{o.significance ?? "—"}</td>
              <td>{o.description ?? "—"}</td>
              <td>{o.first_appearance_chapter ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
