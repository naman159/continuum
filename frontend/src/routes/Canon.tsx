import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Canon() {
  const { novelId } = useParams();
  const queryClient = useQueryClient();
  const [cap] = useChapterCap();
  const [lockedOnly, setLockedOnly] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["canon", novelId, cap, lockedOnly],
    queryFn: () => api.canon(novelId!, cap, lockedOnly),
    enabled: Boolean(novelId),
  });

  const lockMutation = useMutation({
    mutationFn: ({ factId, locked }: { factId: string; locked: boolean }) =>
      api.patchCanonFact(novelId!, factId, { locked }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["canon", novelId, cap, lockedOnly] });
    },
  });

  if (isLoading) return <p>Loading…</p>;

  // Group by subject.
  const bySubject = new Map<string, typeof data>();
  (data ?? []).forEach((f) => {
    const key = f.subject_name ?? "(no subject)";
    const arr = bySubject.get(key) ?? [];
    arr.push(f);
    bySubject.set(key, arr);
  });

  return (
    <div>
      <h1>Canon Facts</h1>
      <p className="muted">
        Locked facts produce hard FAIL from the Continuity Critic on
        contradiction. Unlocked facts produce a WARN.
      </p>
      <label>
        <input
          type="checkbox"
          checked={lockedOnly}
          onChange={(e) => setLockedOnly(e.target.checked)}
        />{" "}
        Locked only
      </label>

      {(!data || data.length === 0) && (
        <p className="muted">No canon facts have been recorded yet.</p>
      )}

      {lockMutation.isError && (
        <p style={{ color: "var(--red-text)", fontSize: 13 }}>Lock update failed: {(lockMutation.error as Error).message}</p>
      )}

      {Array.from(bySubject.entries()).map(([subject, facts]) => (
        <section key={subject} style={{ marginTop: 16 }}>
          <h2>{subject}</h2>
          <table>
            <thead>
              <tr>
                <th>Predicate</th>
                <th>Value</th>
                <th>Kind</th>
                <th>Source ch</th>
                <th>Confidence</th>
                <th>Locked</th>
              </tr>
            </thead>
            <tbody>
              {facts!.map((f) => (
                <tr key={f.id}>
                  <td>
                    <code>{f.predicate}</code>
                  </td>
                  <td>{f.value}</td>
                  <td>
                    <span className="muted">{f.kind}</span>
                  </td>
                  <td>{f.source_chapter ?? "—"}</td>
                  <td>{f.confidence?.toFixed(2) ?? "—"}</td>
                  <td>
                    <button
                      type="button"
                      title={f.locked ? "Unlock (allow extraction to update)" : "Lock (contradictions become flags)"}
                      onClick={() => lockMutation.mutate({ factId: f.id, locked: !f.locked })}
                      disabled={lockMutation.isPending && lockMutation.variables?.factId === f.id}
                      style={{ background: "none", border: "1px solid var(--border)", borderRadius: 4, padding: "2px 8px", cursor: "pointer", color: f.locked ? "var(--accent)" : "var(--text-muted)", fontSize: 12 }}
                    >
                      {f.locked ? "🔒 Locked" : "🔓 Lock"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
