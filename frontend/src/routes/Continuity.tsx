import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

type Tab = "critique" | "flags";

const SEVERITY_CLASS: Record<string, string> = {
  fail: "status-broken",
  warn: "status-pending",
  info: "muted",
};

export default function Continuity() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const [tab, setTab] = useState<Tab>("critique");

  return (
    <div>
      <div className="page-header">
        <h1>Continuity</h1>
      </div>

      <nav style={{ marginBottom: 16 }}>
        {(["critique", "flags"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              marginRight: 8,
              fontWeight: tab === t ? 700 : 400,
              textDecoration: tab === t ? "underline" : "none",
            }}
          >
            {t === "critique" ? "Critique" : "Flags"}
          </button>
        ))}
      </nav>

      {tab === "critique" && <CritiqueTab novelId={novelId!} cap={cap} />}
      {tab === "flags" && <FlagsTab novelId={novelId!} cap={cap} />}
    </div>
  );
}

function FlagsTab({ novelId, cap }: { novelId: string; cap: number | null }) {
  const [resolved, setResolved] = useState<"all" | "open">("all");
  const { data, isLoading } = useQuery({
    queryKey: ["continuity", novelId, cap, resolved],
    queryFn: () => api.continuity(novelId, cap, resolved),
    enabled: Boolean(novelId),
  });

  if (isLoading) return <p className="muted">Loading…</p>;

  return (
    <div>
      <div className="filter-bar">
        <span className="filter-label">Show</span>
        <select value={resolved} onChange={(e) => setResolved(e.target.value as typeof resolved)}>
          <option value="all">All</option>
          <option value="open">Open only</option>
        </select>
      </div>

      {(!data || data.length === 0) && (
        <div className="empty-state">
          <p>No continuity flags.</p>
        </div>
      )}

      {data && data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Chapter</th>
              <th>Description</th>
              <th>Type</th>
              <th>Resolved</th>
            </tr>
          </thead>
          <tbody>
            {data.map((f) => (
              <tr key={f.id}>
                <td>{f.chapter_number}</td>
                <td>{f.description}</td>
                <td>{f.flag_type ?? <span className="muted">—</span>}</td>
                <td>{f.resolved ? `Yes (ch ${f.resolved_chapter_number ?? "?"})` : "No"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function CritiqueTab({ novelId, cap }: { novelId: string; cap: number | null }) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["critique", novelId, cap],
    queryFn: () => api.critique(novelId, cap),
    enabled: Boolean(novelId),
  });

  if (isLoading) return <p className="muted">Loading…</p>;

  if (!data || data.length === 0) {
    return (
      <div className="empty-state">
        <p>No critique reports yet. Process some chapters to run continuity checks.</p>
      </div>
    );
  }

  return (
    <table>
      <thead>
        <tr>
          <th></th>
          <th>Chapter</th>
          <th>Result</th>
          <th>Fails</th>
          <th>Warns</th>
        </tr>
      </thead>
      <tbody>
        {data.map((row) => {
          const isOpen = expanded === row.chapter_number;
          return (
            <Fragment key={row.chapter_number}>
              <tr
                onClick={() => setExpanded(isOpen ? null : row.chapter_number)}
                style={{ cursor: "pointer" }}
              >
                <td style={{ width: 20 }}>{isOpen ? "▾" : "▸"}</td>
                <td>ch {row.chapter_number}</td>
                <td>
                  <span className={row.passed ? "status-satisfied" : "status-broken"}>
                    {row.passed ? "Passed" : "Failed"}
                  </span>
                </td>
                <td>{row.fails}</td>
                <td>{row.warns}</td>
              </tr>
              {isOpen && (
                <tr>
                  <td colSpan={5}>
                    <CritiqueDetail novelId={novelId} chapterNumber={row.chapter_number} />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function CritiqueDetail({
  novelId,
  chapterNumber,
}: {
  novelId: string;
  chapterNumber: number;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["critique-detail", novelId, chapterNumber],
    queryFn: () => api.critiqueDetail(novelId, chapterNumber),
  });

  if (isLoading) return <p className="muted">Loading findings…</p>;
  if (!data || data.findings.length === 0) return <p className="muted">No findings.</p>;

  return (
    <table style={{ margin: "8px 0" }}>
      <thead>
        <tr>
          <th>Check</th>
          <th>Severity</th>
          <th>Message</th>
          <th>Quote</th>
        </tr>
      </thead>
      <tbody>
        {data.findings.map((f, i) => (
          <tr key={i}>
            <td>{f.check_name}</td>
            <td>
              <span className={SEVERITY_CLASS[f.severity] ?? ""}>{f.severity}</span>
            </td>
            <td>{f.message}</td>
            <td style={{ fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: 12 }}>
              {f.quote ?? <span className="muted">—</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
