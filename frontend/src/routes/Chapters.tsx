import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Chapters() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const { data, isLoading } = useQuery({
    queryKey: ["chapters", novelId, cap],
    queryFn: () => api.chapters(novelId!, cap),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p className="muted">Loading…</p>;
  return (
    <div>
      <h1>Chapters</h1>
      <table>
        <thead>
          <tr><th>#</th><th>Title</th><th>Summary</th><th>Processed</th></tr>
        </thead>
        <tbody>
          {data?.map((c) => (
            <tr key={c.id}>
              <td>{c.number}</td>
              <td>{c.title ?? "—"}</td>
              <td>{c.summary_short ?? c.summary ?? "—"}</td>
              <td>{c.processed_at ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
