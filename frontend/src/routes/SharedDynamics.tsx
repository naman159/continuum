import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function SharedDynamics() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();

  const { data, isLoading, error } = useQuery({
    queryKey: ["dynamics", novelId, cap],
    queryFn: () => api.dynamics(novelId!, cap),
    enabled: Boolean(novelId),
  });

  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;

  return (
    <div>
      <h1>Shared Dynamics</h1>
      <p className="muted">Per-chapter relational climate between entity pairs.</p>
      {!data || data.length === 0 ? (
        <p className="muted">No dynamics recorded within cap.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Chapter</th>
              <th>Entity A</th>
              <th>Entity B</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.id}>
                <td>{d.chapter_number}</td>
                <td>{d.entity_a_name}</td>
                <td>{d.entity_b_name}</td>
                <td>{d.description ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
