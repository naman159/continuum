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

  const resolveMutation = useMutation({
    mutationFn: async (action: "accept" | "reject") => {
      if (action === "accept") return api.acceptDraft(selectedId!, note, edited ?? undefined);
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
  const selected: DraftDetail | null = detailQuery.data ?? null;
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
    <div className="page">
      <h1>Review queue</h1>
      <p className="muted">
        Drafts submitted by a writing agent that failed continuity. Accepting
        one records its findings against the chapter as an override.
      </p>

      {error && <div className="error">{error}</div>}

      {rows.length === 0 && <p className="muted">No drafts awaiting review.</p>}

      <ul className="draft-list">
        {rows.map((r) => (
          <li key={r.id}>
            <button type="button" onClick={() => open(r.id)}>
              Chapter {r.chapter_number}
              {r.title ? ` — ${r.title}` : ""}
              <span className="badge badge-fail">{r.fail_count} fail</span>
              <span className="badge">{r.warn_count} warn</span>
            </button>
          </li>
        ))}
      </ul>

      {selected && (
        <section className="draft-detail">
          <h2>
            Chapter {selected.chapter_number}
            {selected.title ? ` — ${selected.title}` : ""}
          </h2>

          <h3>Findings</h3>
          {selected.findings.error && (
            <p className="error">Critic error: {selected.findings.error}</p>
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

          <h3>Draft</h3>
          <textarea
            value={edited ?? selected.raw_text}
            onChange={(e) => setEdited(e.target.value)}
            rows={20}
          />

          <label htmlFor="review-note">Resolution note</label>
          <input
            id="review-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why are you accepting or rejecting this?"
          />

          <div className="actions">
            <button type="button" disabled={busy} onClick={() => resolveMutation.mutate("accept")}>
              {edited === null ? "Accept" : "Accept with edits"}
            </button>
            <button type="button" disabled={busy} onClick={() => resolveMutation.mutate("reject")}>
              Reject
            </button>
            <button type="button" disabled={busy} onClick={() => setSelectedId(null)}>
              Cancel
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
