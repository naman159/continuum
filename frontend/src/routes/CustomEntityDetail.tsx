import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";

export default function CustomEntityDetail() {
  const { novelId, entityId } = useParams<{ novelId: string; entityId: string }>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["custom-entity", novelId, entityId],
    queryFn: () => api.customEntity(novelId!, entityId!),
    enabled: Boolean(novelId && entityId),
  });

  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {String(error instanceof Error ? error.message : error)}</p>;
  if (!data) return <p>Not found.</p>;

  const typeLabel = data.entity_type.replace(/_/g, " ");

  return (
    <div>
      <h1>{data.name}</h1>
      <p style={{ color: "var(--text-muted)", textTransform: "capitalize" }}>{typeLabel}</p>
      {data.description && <p>{data.description}</p>}

      {data.relationships.length > 0 && (
        <>
          <h2>Relationships</h2>
          <table>
            <thead>
              <tr>
                <th>Entity</th>
                <th>Type</th>
                <th>Relation</th>
                <th>Direction</th>
                <th>From Ch.</th>
              </tr>
            </thead>
            <tbody>
              {data.relationships.map((r, i) => (
                <tr key={`${r.other_entity_name}-${r.direction}-${r.rel_type ?? ""}-${r.from_chapter ?? ""}-${i}`}>
                  <td>{r.other_entity_name}</td>
                  <td style={{ textTransform: "capitalize" }}>{r.other_entity_type.replace(/_/g, " ")}</td>
                  <td>{r.rel_type ?? "—"}</td>
                  <td>{r.direction === "from" ? "→" : "←"}</td>
                  <td>{r.from_chapter ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
