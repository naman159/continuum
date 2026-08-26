import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { api, type DraftDetail } from "../api";

export default function Review() {
  const { novelId = "" } = useParams();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [edited, setEdited] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: ["drafts", novelId],
    queryFn: () => api.drafts(novelId),
  });

  const detailQuery = useQuery({
    queryKey: ["draft", selectedId],
    queryFn: () => api.draft(selectedId!),
    enabled: selectedId !== null,
  });

  const selected: DraftDetail | null = detailQuery.data ?? null;
  // "edited" only means something once the text actually diverges from the
  // draft's raw_text — typing into the textarea and then reverting it back
  // to the original must not be recorded as a human edit.
  const isEdited = edited !== null && edited !== selected?.raw_text;

  const resolveMutation = useMutation({
    mutationFn: async (action: "accept" | "reject") => {
      if (action === "accept") {
        return api.acceptDraft(selectedId!, note, isEdited ? edited! : undefined);
      }
      return api.rejectDraft(selectedId!, note);
    },
    onSuccess: () => {
      setSelectedId(null);
      setNote("");
      setEdited(null);
      void queryClient.invalidateQueries({ queryKey: ["drafts", novelId] });
    },
  });

  const rows = listQuery.data ?? [];
  const busy = resolveMutation.isPending;
  const failure = listQuery.error ?? detailQuery.error ?? resolveMutation.error;
  const error = failure
    ? failure instanceof Error
      ? failure.message
      : String(failure)
    : null;

  function open(id: string) {
    setNote("");
    setEdited(null);
    setSelectedId(id);
  }

  return (
    <div>
      <div className="page-header">
        <h1>Review queue</h1>
      </div>
      <p className="muted" style={{ marginBottom: 16 }}>
        Drafts submitted by a writing agent that failed continuity. Accepting
        one records its findings against the chapter as an override.
      </p>

      {error && <p className="status-error">{error}</p>}

      {rows.length === 0 && (
        <div className="empty-state">
          <p>No drafts awaiting review.</p>
        </div>
      )}

      {rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Chapter</th>
              <th>Fails</th>
              <th>Warns</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                onClick={() => open(r.id)}
                style={{ cursor: "pointer", fontWeight: selectedId === r.id ? 700 : 400 }}
              >
                <td>
                  Chapter {r.chapter_number}
                  {r.title ? ` — ${r.title}` : ""}
                </td>
                <td>
                  <span className="badge status-error">{r.fail_count} fail</span>
                </td>
                <td>
                  <span className="badge">{r.warn_count} warn</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selected && (
        <div className="form-panel" style={{ marginTop: 24 }}>
          <div className="form-panel-title">
            Chapter {selected.chapter_number}
            {selected.title ? ` — ${selected.title}` : ""}
          </div>

          <div className="form-group">
            <span className="form-label">Findings</span>
            {selected.findings.error && (
              <p className="status-error">Critic error: {selected.findings.error}</p>
            )}
            <ul>
              {(selected.findings.fails ?? []).map((f, i) => (
                <li key={`f${i}`}>
                  <strong>{f.check}</strong>: {f.message}
                  {f.quote && <blockquote>{f.quote}</blockquote>}
                </li>
              ))}
              {(selected.findings.warns ?? []).map((f, i) => (
                <li key={`w${i}`} className="muted">
                  <strong>{f.check}</strong> (warn): {f.message}
                </li>
              ))}
            </ul>
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="review-draft">
              Draft
            </label>
            <textarea
              id="review-draft"
              value={edited ?? selected.raw_text}
              onChange={(e) => setEdited(e.target.value)}
              rows={20}
            />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="review-note">
              Resolution note
            </label>
            <input
              id="review-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Why are you accepting or rejecting this?"
            />
          </div>

          <div className="form-actions">
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              onClick={() => resolveMutation.mutate("accept")}
            >
              {isEdited ? "Accept with edits" : "Accept"}
            </button>
            <button
              type="button"
              className="btn-danger"
              disabled={busy}
              onClick={() => resolveMutation.mutate("reject")}
            >
              Reject
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={busy}
              onClick={() => setSelectedId(null)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
