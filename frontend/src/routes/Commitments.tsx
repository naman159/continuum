import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

const STATUS_COLORS: Record<string, string> = {
  pending: "#b8860b",
  satisfied: "#2c5f5d",
  broken: "#b3261e",
  abandoned: "#6b7280",
};

export default function Commitments() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const [status, setStatus] = useState<
    "all" | "pending" | "satisfied" | "broken" | "abandoned"
  >("all");
  const { data, isLoading } = useQuery({
    queryKey: ["commitments", novelId, cap, status],
    queryFn: () => api.commitments(novelId!, cap, status),
    enabled: Boolean(novelId),
  });

  if (isLoading) return <p>Loading…</p>;

  return (
    <div>
      <h1>Commitments</h1>
      <p className="muted">
        Foreshadow → payoff triples (CFPG). Pending commitments are the
        Chekhov's guns still on the wall.
      </p>
      <label>
        Status:{" "}
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as typeof status)}
        >
          <option value="all">All</option>
          <option value="pending">Pending</option>
          <option value="satisfied">Satisfied</option>
          <option value="broken">Broken</option>
          <option value="abandoned">Abandoned</option>
        </select>
      </label>

      {(!data || data.length === 0) && (
        <p className="muted">No commitments extracted yet.</p>
      )}

      <table style={{ marginTop: 16, width: "100%" }}>
        <thead>
          <tr>
            <th>Status</th>
            <th>Foreshadow</th>
            <th>Planted</th>
            <th>Payoff</th>
            <th>Age (ch)</th>
            <th>Weight</th>
            <th>Related</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((c) => (
            <tr key={c.id}>
              <td>
                <span
                  style={{
                    color: STATUS_COLORS[c.status] ?? "inherit",
                    fontWeight: 600,
                  }}
                >
                  {c.status}
                </span>
              </td>
              <td>{c.foreshadow_text}</td>
              <td>{c.foreshadow_chapter}</td>
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
              <td>{c.age_chapters ?? "—"}</td>
              <td>{c.weight?.toFixed(2) ?? "—"}</td>
              <td>{c.related_entity_names.join(", ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
