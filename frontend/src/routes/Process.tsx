import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

type JobStatus = {
  job_id: string;
  status: "pending" | "running" | "done" | "error";
  current_pass: string | null;
  passes_done: number;
  total_passes: number;
  result: Record<string, unknown> | null;
  error: string | null;
};

async function postChapter(
  novelId: string,
  number: number,
  text: string
): Promise<{ job_id: string }> {
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

export default function Process() {
  const { novelId } = useParams();
  const [chapterNumber, setChapterNumber] = useState<number>(1);
  const [text, setText] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: ({ number, text }: { number: number; text: string }) =>
      postChapter(novelId!, number, text),
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

  return (
    <div>
      <h1>Process Chapter</h1>

      {!jobId && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            mutation.mutate({ number: chapterNumber, text });
          }}
        >
          <div style={{ marginBottom: 12 }}>
            <label htmlFor="chap-number">Chapter number</label>
            <br />
            <input
              id="chap-number"
              type="number"
              min={1}
              value={chapterNumber}
              onChange={(e) => setChapterNumber(Number(e.target.value))}
              style={{ width: 80, marginTop: 4 }}
            />
          </div>
          <div style={{ marginBottom: 12 }}>
            <label htmlFor="chap-text">Chapter text</label>
            <br />
            <textarea
              id="chap-text"
              rows={20}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Paste chapter text here…"
              style={{ width: "100%", marginTop: 4, fontFamily: "monospace", fontSize: 13 }}
            />
          </div>
          {mutation.isError && (
            <p style={{ color: "red" }}>Error: {(mutation.error as Error).message}</p>
          )}
          <button type="submit" disabled={mutation.isPending || !text.trim()}>
            {mutation.isPending ? "Submitting…" : "Process chapter"}
          </button>
        </form>
      )}

      {jobId && (
        <div>
          <p className="muted">
            Job ID: <code>{jobId}</code>
          </p>

          {isRunning && (
            <div>
              <p>⏳ Processing…</p>
              {job && (
                <p>
                  {job.current_pass
                    ? `Running pass: ${job.current_pass}`
                    : "Starting…"}
                  {job.total_passes > 0 &&
                    ` (${job.passes_done} / ${job.total_passes})`}
                </p>
              )}
              <progress
                value={job?.passes_done ?? 0}
                max={job?.total_passes ?? 1}
                style={{ width: "100%" }}
              />
            </div>
          )}

          {job?.status === "done" && (
            <div>
              <p style={{ color: "green" }}>✓ Done!</p>
              {job.result && (
                <ul>
                  <li>
                    {(job.result as Record<string, number>).new_characters ?? 0} new
                    characters
                  </li>
                  <li>
                    {(job.result as Record<string, number>).events ?? 0} events
                  </li>
                  <li>
                    {(job.result as Record<string, number>).thread_updates ?? 0} thread
                    updates
                  </li>
                  <li>
                    {(job.result as Record<string, number>).continuity_flags ?? 0}{" "}
                    continuity flags
                  </li>
                </ul>
              )}
              <p>
                <Link to={`/novels/${novelId}/characters`}>View Characters</Link>
                {" · "}
                <Link to={`/novels/${novelId}/timeline`}>View Timeline</Link>
              </p>
              <button
                onClick={() => {
                  setJobId(null);
                  setText("");
                }}
              >
                Process another chapter
              </button>
            </div>
          )}

          {job?.status === "error" && (
            <div>
              <p style={{ color: "red" }}>✗ Error</p>
              <pre
                style={{
                  background: "#fee",
                  padding: 8,
                  borderRadius: 4,
                  whiteSpace: "pre-wrap",
                }}
              >
                {job.error}
              </pre>
              <button onClick={() => setJobId(null)}>Try again</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
