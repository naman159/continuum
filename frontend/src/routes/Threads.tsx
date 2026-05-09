import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Threads() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const [status, setStatus] = useState<"all" | "open" | "progressing" | "closed">("all");
  const { data, isLoading } = useQuery({
    queryKey: ["threads", novelId, cap, status],
    queryFn: () => api.threads(novelId!, cap, status),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  return (
    <div>
      <h1>Threads</h1>
      <label>
        Status:{" "}
        <select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
          <option value="all">All</option>
          <option value="open">Open</option>
          <option value="progressing">Progressing</option>
          <option value="closed">Closed</option>
        </select>
      </label>
      {data?.map((t) => (
        <details key={t.id} open>
          <summary>
            <strong>{t.title}</strong> — {t.status} ({t.thread_type ?? "?"})
          </summary>
          {t.description && <p>{t.description}</p>}
          <p className="muted">
            Opened ch {t.opened_chapter ?? "—"} · Closed ch {t.closed_chapter ?? "—"}
          </p>
          <table>
            <thead><tr><th>Chapter</th><th>Event</th><th>Impact</th></tr></thead>
            <tbody>
              {t.events.map((e) => (
                <tr key={e.event_id}><td>{e.chapter_number}</td><td>{e.description}</td><td>{e.impact ?? "—"}</td></tr>
              ))}
            </tbody>
          </table>
        </details>
      ))}
    </div>
  );
}
