import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Zap, Check, X } from "lucide-react";

import { fetchJson, postJson } from "../api";

type JobStatus = {
  job_id: string;
  status: "pending" | "running" | "done" | "error";
  current_pass: string | null;
  passes_done: number;
  total_passes: number;
  result: Record<string, unknown> | null;
  error: string | null;
};

function postChapter(
  novelId: string,
  number: number,
  text: string,
  replace: boolean
): Promise<{ job_id: string }> {
  return postJson<{ job_id: string }>(`/api/novels/${novelId}/chapters/process`, {
    number,
    text,
    replace,
  });
}

function fetchJob(jobId: string): Promise<JobStatus> {
  return fetchJson<JobStatus>(`/api/jobs/${jobId}`);
}

const PASS_LABELS: Record<string, string> = {
  chapter_summary: "Summarising chapter",
  new_entities: "Extracting new entities",
  state_deltas: "Tracking character changes",
  events: "Extracting events",
  thread_updates: "Updating plot threads",
  continuity_flags: "Checking continuity",
  relationship_updates: "Mapping relationships",
  dynamics_updates: "Tracking dynamics",
  scene_segmentation: "Segmenting scenes",
  multi_granularity_summaries: "Generating summaries",
  knowledge_state_deltas: "Tracking knowledge",
  commitments: "Tracking commitments",
  intra_dedup: "Deduplicating entities",
  canonicalization: "Canonicalising names",
  canon_facts: "Extracting canon facts",
};

export default function Process() {
  const { novelId } = useParams();
  const [chapterNumber, setChapterNumber] = useState<number>(1);
  const [text, setText] = useState("");
  const [replace, setReplace] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: ({ number, text, replace }: { number: number; text: string; replace: boolean }) =>
      postChapter(novelId!, number, text, replace),
    onSuccess: (data) => setJobId(data.job_id),
  });

  const jobQuery = useQuery<JobStatus>({
    queryKey: ["job", jobId],
    queryFn: () => fetchJob(jobId!),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "done" || s === "error" || query.state.status === "error" ? false : 2000;
    },
  });

  const job = jobQuery.data;
  const isRunning = Boolean(jobId) && !jobQuery.isError && job?.status !== "done" && job?.status !== "error";

  const result = job?.result as Record<string, unknown> | null | undefined;
  const rebuildResults = result?.rebuild_results as Record<string, unknown>[] | undefined;
  const enrichmentWarnings = (rebuildResults ?? (result ? [result] : [])).flatMap((item, index) => {
    const enrichment = item.enrichment as { warnings?: string[] } | undefined;
    return (enrichment?.warnings ?? []).map((warning) =>
      rebuildResults ? `Chapter ${chapterNumber + index}: ${warning}` : warning,
    );
  });
  const rebuiltChapters = result?.rebuilt_chapters as number[] | undefined;
  const critique = result?.critique as {
    status?: string; error?: string; persisted?: boolean;
  } | null | undefined;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
        <h1 style={{ margin: 0 }}>Process Chapter</h1>
      </div>

      {!jobId && (
        <form
          className="process-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            mutation.mutate({ number: chapterNumber, text, replace });
          }}
          style={{ maxWidth: 680 }}
        >
          <div className="form-group">
            <label className="form-label" htmlFor="chap-number">Chapter number</label>
            <input
              id="chap-number"
              type="number"
              min={1}
              value={chapterNumber}
              onChange={(e) => setChapterNumber(Number(e.target.value))}
              style={{ width: 100 }}
            />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="chap-text">Chapter text</label>
            <textarea
              id="chap-text"
              rows={22}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Paste your chapter text here…"
            />
          </div>

          <div className="form-group">
            <label className="form-label" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="checkbox"
                checked={replace}
                onChange={(e) => setReplace(e.target.checked)}
                style={{ width: "auto" }}
              />
              Replace existing chapter (deletes the chapter&apos;s previous extraction and
              rebuilds this chapter and all later chapters; additional model calls apply)
            </label>
          </div>

          {mutation.isError && (
            <p style={{ color: "var(--red-text)", fontSize: 13, marginBottom: 12 }}>
              Error: {(mutation.error as Error).message}
            </p>
          )}

          <button
            type="submit"
            className="btn-primary"
            disabled={mutation.isPending || !text.trim()}
          >
            <Zap size={14} />
            {mutation.isPending ? "Submitting…" : "Process chapter"}
          </button>
        </form>
      )}

      {jobId && (
        <div className="job-card">
          <div className="job-id">
            <span>Job</span>
            <code>{jobId}</code>
          </div>

          {jobQuery.isError && (
            <div role="alert">
              <p className="status-error">Could not retrieve processing status: {jobQuery.error.message}</p>
              <p className="muted">Your chapter text is preserved. Check Chapters before submitting again.</p>
              <button onClick={() => jobQuery.refetch()}>Retry status check</button>
              <button onClick={() => setJobId(null)}>Back to draft</button>
            </div>
          )}

          {isRunning && (
            <div>
              <div className="job-running-indicator">
                <span className="pulse-dot" />
                <span className="job-pass-label">
                  {job?.current_pass
                    ? (PASS_LABELS[job.current_pass] ?? job.current_pass)
                    : "Starting…"}
                </span>
              </div>

              {job && job.total_passes > 0 && (
                <div className="job-pass-count" style={{ marginBottom: 10 }}>
                  Pass {job.passes_done} of {job.total_passes}
                </div>
              )}

              {job && job.total_passes > 0 ? (
                <progress value={job.passes_done} max={job.total_passes} />
              ) : (
                <progress />
              )}
            </div>
          )}

          {job?.status === "done" && (
            <div>
              <div className="job-success-header">
                <div className="job-success-icon">
                  <Check />
                </div>
                <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-h)" }}>
                  Chapter processed
                </span>
              </div>

              {rebuiltChapters && rebuiltChapters.length > 1 && (
                <p>Rebuilt chapters {rebuiltChapters.join(", ")} from the updated story context.</p>
              )}
              {enrichmentWarnings.length > 0 && (
                <div role="alert" className="status-error">
                  <p>The chapter was saved with items that need review:</p>
                  <ul>{enrichmentWarnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
                </div>
              )}
              {result?.materialized === false && (
                <p role="alert" className="status-error">
                  The chapter was saved, but rebuilding character state failed.
                  Rebuild state before relying on the character and knowledge views.
                </p>
              )}
              {critique && critique.status !== "ok" && (
                <p role="alert" className="muted">
                  The chapter was saved without a completed continuity review.
                  {critique.error ? ` ${critique.error}` : ""}
                </p>
              )}
              {critique?.persisted === false && (
                <p role="alert" className="status-error">
                  The chapter was saved, but its continuity report could not be
                  stored. Run the continuity review again to restore the report.
                </p>
              )}

              {result && (
                <div className="job-result">
                  {([
                    ["New characters", result.new_characters],
                    ["Events extracted", result.events],
                    ["Thread updates", result.thread_updates],
                    ["Continuity flags", result.continuity_flags],
                  ] as [string, unknown][]).map(([label, value]) => (
                    <div key={label} className="job-result-row">
                      <span style={{ color: "var(--text-muted)" }}>{label}</span>
                      <span className="job-result-value">{(value as number) ?? 0}</span>
                    </div>
                  ))}
                </div>
              )}

              <div className="job-links">
                <Link to={`/novels/${novelId}/characters`} style={{ color: "var(--accent-text)" }}>
                  View Characters →
                </Link>
                <Link to={`/novels/${novelId}/timeline`} style={{ color: "var(--accent-text)" }}>
                  View Timeline →
                </Link>
              </div>

              <div style={{ marginTop: 20 }}>
                <button
                  onClick={() => {
                    setJobId(null);
                    setText("");
                    setReplace(false);
                    setChapterNumber((n) => n + 1);
                  }}
                >
                  Process another chapter
                </button>
              </div>
            </div>
          )}

          {job?.status === "error" && (
            <div>
              <div className="job-error-header">
                <div className="job-error-icon">
                  <X />
                </div>
                <span style={{ fontSize: 14, fontWeight: 600, color: "var(--red-text)" }}>
                  Processing failed
                </span>
              </div>
              <pre className="job-error-pre">{job.error}</pre>
              <button onClick={() => setJobId(null)}>Try again</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
