import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Continuity() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const [resolved, setResolved] = useState<"all" | "open">("all");
  const { data, isLoading } = useQuery({
    queryKey: ["continuity", novelId, cap, resolved],
    queryFn: () => api.continuity(novelId!, cap, resolved),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  return (
    <div>
      <h1>Continuity flags</h1>
      <label>
        Show:{" "}
        <select value={resolved} onChange={(e) => setResolved(e.target.value as typeof resolved)}>
          <option value="all">All</option>
          <option value="open">Open only</option>
        </select>
      </label>
      <table>
        <thead><tr><th>Chapter</th><th>Description</th><th>Type</th><th>Resolved</th></tr></thead>
        <tbody>
          {data?.map((f) => (
            <tr key={f.id}>
              <td>{f.chapter_number}</td>
              <td>{f.description}</td>
              <td>{f.flag_type ?? "—"}</td>
              <td>{f.resolved ? `Yes (ch ${f.resolved_chapter_number ?? "?"})` : "No"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
