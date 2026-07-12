import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

const STATUS_CLASS: Record<string, string> = {
  pending: "status-pending",
  satisfied: "status-satisfied",
  broken: "status-broken",
  abandoned: "status-abandoned",
};

export default function Commitments() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const [status, setStatus] = useState<"all" | "pending" | "satisfied" | "broken" | "abandoned">("all");

  const { data, isLoading } = useQuery({
    queryKey: ["commitments", novelId, cap, status],
    queryFn: () => api.commitments(novelId!, cap, status),
    enabled: Boolean(novelId),
  });

  if (isLoading) return <p className="muted">Loading…</p>;

  return (
    <div>
      <div className="page-header">
        <h1>Commitments</h1>
      </div>
      <p className="muted" style={{ marginBottom: 16 }}>
        Foreshadow → payoff triples. Pending commitments are the Chekhov's guns still on the wall.
      </p>

      <div className="filter-bar">
        <span className="filter-label">Status</span>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as typeof status)}
          style={{ width: "auto" }}
        >
          <option value="all">All</option>
          <option value="pending">Pending</option>
          <option value="satisfied">Satisfied</option>
          <option value="broken">Broken</option>
          <option value="abandoned">Abandoned</option>
        </select>
      </div>

      {(!data || data.length === 0) && (
        <div className="empty-state">
          <p>No commitments extracted yet.</p>
        </div>
      )}

      {data && data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Foreshadow</th>
              <th>Planted</th>
              <th>Payoff</th>
              <th>Age</th>
              <th>Weight</th>
              <th>Related</th>
            </tr>
          </thead>
          <tbody>
            {data.map((c) => (
              <tr key={c.id}>
                <td>
                  <span className={STATUS_CLASS[c.status_at_cutoff] ?? ""}>
                    {c.status_at_cutoff}
                  </span>
                  {c.status_at_cutoff !== c.status && (
                    <span className="muted" style={{ marginLeft: 6 }}>
                      (pays off ch {c.payoff_chapter ?? "?"})
                    </span>
                  )}
                </td>
                <td>{c.foreshadow_text}</td>
                <td style={{ whiteSpace: "nowrap" }}>ch {c.foreshadow_chapter}</td>
                <td>
                  {c.payoff_text ? (
                    <>
                      {c.payoff_text}{" "}
                      <span className="muted">(ch {c.payoff_chapter})</span>
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {c.age_chapters != null ? `${c.age_chapters} ch` : <span className="muted">—</span>}
                </td>
                <td>{c.weight?.toFixed(2) ?? <span className="muted">—</span>}</td>
                <td style={{ fontSize: 12 }}>{c.related_entity_names.join(", ") || <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
