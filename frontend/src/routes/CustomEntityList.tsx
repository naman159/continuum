import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";

export default function CustomEntityList() {
  const { novelId, typeName } = useParams<{ novelId: string; typeName: string }>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["custom-entities", novelId, typeName],
    queryFn: () => api.customEntities(novelId!, typeName!),
    enabled: Boolean(novelId && typeName),
  });

  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {String(error instanceof Error ? error.message : error)}</p>;

  const label = typeName ? typeName.replace(/_/g, " ") : "";
  const capitalized = label.charAt(0).toUpperCase() + label.slice(1);
  const displayLabel = capitalized.endsWith("s") ? capitalized : capitalized + "s";

  return (
    <div>
      <h1>{displayLabel}</h1>
      {(!data || data.length === 0) && <p>None found.</p>}
      {data && data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {data.map((e) => (
              <tr key={e.id}>
                <td>
                  <Link to={`/novels/${novelId}/custom-entities/${e.id}`}>{e.name}</Link>
                </td>
                <td>{e.description ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
