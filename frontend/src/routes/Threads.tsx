import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

const STATUS_CLASS: Record<string, string> = {
  open: "status-open",
  progressing: "status-progressing",
  closed: "status-closed",
};

export default function Threads() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const [status, setStatus] = useState<"all" | "open" | "progressing" | "closed">("all");

  const { data, isLoading } = useQuery({
    queryKey: ["threads", novelId, cap, status],
    queryFn: () => api.threads(novelId!, cap, status),
    enabled: Boolean(novelId),
  });

  if (isLoading) return <p className="muted">Loading…</p>;

  return (
    <div>
      <div className="page-header">
        <h1>Threads</h1>
      </div>

      <div className="filter-bar">
        <span className="filter-label">Status</span>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as typeof status)}
          style={{ width: "auto" }}
        >
          <option value="all">All</option>
          <option value="open">Open</option>
          <option value="progressing">Progressing</option>
          <option value="closed">Closed</option>
        </select>
      </div>

      {(!data || data.length === 0) && (
        <div className="empty-state"><p>No threads found.</p></div>
      )}

      {data?.map((t) => (
        <details key={t.id} open>
          <summary>
            <strong style={{ color: "var(--text-h)" }}>{t.title}</strong>
            <span className={`muted ${STATUS_CLASS[t.status_at_cutoff] ?? ""}`} style={{ marginLeft: 8 }}>
              {t.status_at_cutoff}
            </span>
            {t.status_at_cutoff !== t.status && (
              <span className="muted" style={{ marginLeft: 6 }}>
                (closes ch {t.closed_chapter ?? "?"})
              </span>
            )}
            {t.thread_type && (
              <span className="tag" style={{ marginLeft: 6 }}>{t.thread_type}</span>
            )}
          </summary>
          {t.description && (
            <p style={{ marginBottom: 10, lineHeight: 1.6 }}>{t.description}</p>
          )}
          <p className="muted" style={{ marginBottom: 10 }}>
            Opened ch {t.opened_chapter ?? "—"} · Closed ch {t.closed_chapter ?? "—"}
          </p>
          {t.events.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Chapter</th>
                  <th>Event</th>
                  <th>Impact</th>
                </tr>
              </thead>
              <tbody>
                {t.events.map((e) => (
                  <tr key={e.event_id}>
                    <td style={{ whiteSpace: "nowrap" }}>ch {e.chapter_number}</td>
                    <td>{e.description}</td>
                    <td>{e.impact ?? <span className="muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </details>
      ))}
    </div>
  );
}
