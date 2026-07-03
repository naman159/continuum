import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";

type JobStatus = {
  job_id: string;
  status: "pending" | "running" | "done" | "error";
  current_pass: string | null;
  passes_done: number;
  total_passes: number;
  result: Record<string, unknown> | null;
  error: string | null;
};

async function postChapter(novelId: string, number: number, text: string): Promise<{ job_id: string }> {
  const res = await fetch(`/api/novels/${novelId}/chapters/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ number, text }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

async function fetchJob(jobId: string): Promise<JobStatus> {
  const res = await fetch(`/api/jobs/${jobId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

const PASS_LABELS: Record<string, string> = {
  chapter_summary: "Summarising chapter",
  new_entities: "Extracting new entities",
  entity_deltas: "Tracking character changes",
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

function ZapIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function XIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

export default function Process() {
  const { novelId } = useParams();
  const [chapterNumber, setChapterNumber] = useState<number>(1);
  const [text, setText] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [mode, setMode] = useState<"process" | "generate">("process");
  const [ingestGenerated, setIngestGenerated] = useState(false);

  const mutation = useMutation({
    mutationFn: ({ number, text }: { number: number; text: string }) =>
      postChapter(novelId!, number, text),
    onSuccess: (data) => setJobId(data.job_id),
  });

  const generateMutation = useMutation({
    mutationFn: ({ number, ingest }: { number: number; ingest: boolean }) =>
      api.generateChapter(novelId!, number, ingest),
    onSuccess: (data) => setJobId(data.job_id),
  });

  const jobQuery = useQuery<JobStatus>({
    queryKey: ["job", jobId],
    queryFn: () => fetchJob(jobId!),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "done" || s === "error" ? false : 2000;
    },
  });

  const job = jobQuery.data;
  const isRunning = Boolean(jobId) && job?.status !== "done" && job?.status !== "error";

  const result = job?.result as Record<string, unknown> | null | undefined;
  const isGenerationResult = typeof result?.text === "string";

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
        <h1 style={{ margin: 0 }}>{mode === "generate" ? "Generate Chapter" : "Process Chapter"}</h1>
      </div>

      {!jobId && (
        <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          <button onClick={() => setMode("process")} disabled={mode === "process"}>
            Ingest text
          </button>
          <button onClick={() => setMode("generate")} disabled={mode === "generate"}>
            Generate chapter
          </button>
        </div>
      )}

      {!jobId && mode === "generate" && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            generateMutation.mutate({ number: chapterNumber, ingest: ingestGenerated });
          }}
          style={{ maxWidth: 480 }}
        >
          <div className="form-group">
            <label className="form-label" htmlFor="gen-number">Chapter number</label>
            <input
              id="gen-number"
              type="number"
              min={1}
              value={chapterNumber}
              onChange={(e) => setChapterNumber(Number(e.target.value))}
              style={{ width: 100 }}
            />
          </div>
          <label style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
            <input
              type="checkbox"
              checked={ingestGenerated}
              onChange={(e) => setIngestGenerated(e.target.checked)}
            />
            Ingest into Continuum if the continuity critic passes
          </label>
          {generateMutation.isError && (
            <p style={{ color: "var(--red-text)", fontSize: 13, marginBottom: 12 }}>
              Error: {(generateMutation.error as Error).message}
            </p>
          )}
          <button type="submit" className="btn-primary" disabled={generateMutation.isPending}>
            <ZapIcon />
            {generateMutation.isPending ? "Submitting…" : "Generate chapter"}
          </button>
        </form>
      )}

      {!jobId && mode === "process" && (
        <form
          className="process-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            mutation.mutate({ number: chapterNumber, text });
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
            <ZapIcon />
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
                // Generation jobs report total_passes=0 (label-only progress);
                // a value-less <progress> renders as indeterminate instead of
                // a bar pegged at an invalid max of 0.
                <progress />
              )}
            </div>
          )}

          {job?.status === "done" && (
            <div>
              <div className="job-success-header">
                <div className="job-success-icon">
                  <CheckIcon />
                </div>
                <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-h)" }}>
                  {isGenerationResult ? "Chapter generated" : "Chapter processed"}
                </span>
              </div>

              {result && !isGenerationResult && (
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

              {result && isGenerationResult && (
                <div style={{ marginTop: 4 }}>
                  <div className="job-pass-count" style={{ marginBottom: 8 }}>
                    Critic: {result.passed ? "passed" : `failed (${result.fails as number} blocking)`}
                    {" · "}{result.iterations as number} iteration(s)
                    {" · "}{result.ingested ? "ingested into Continuum" : "not ingested"}
                  </div>
                  <pre style={{ whiteSpace: "pre-wrap", maxHeight: 400, overflow: "auto" }}>
                    {result.text as string}
                  </pre>
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
                  <XIcon />
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
