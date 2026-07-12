import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Search() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["search", novelId, cap, query],
    queryFn: () => api.search(novelId!, query, cap),
    enabled: Boolean(novelId) && query.trim().length > 0,
  });

  const submit = () => setQuery(input.trim());

  return (
    <div>
      <div className="page-header">
        <h1>Search</h1>
      </div>
      <p className="muted" style={{ marginBottom: 16 }}>
        Hybrid (semantic + keyword) search over chapters, scenes, and events, bounded by the
        chapter cap.
      </p>

      <div style={{ maxWidth: 480, marginBottom: 20 }}>
        <input
          type="text"
          value={input}
          placeholder="Search chapters, events, facts…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          aria-label="Search query"
        />
      </div>

      {query.trim() === "" && (
        <div className="empty-state">
          <p>Enter a search term and press Enter.</p>
        </div>
      )}

      {query.trim() !== "" && isLoading && <p className="muted">Loading…</p>}

      {query.trim() !== "" && !isLoading && (!data || data.results.length === 0) && (
        <div className="empty-state">
          <p>No results for &ldquo;{query}&rdquo;.</p>
        </div>
      )}

      {data && data.results.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Kind</th>
              <th>Chapter</th>
              <th>Score</th>
              <th>Snippet</th>
            </tr>
          </thead>
          <tbody>
            {data.results.map((r, i) => (
              <tr key={i}>
                <td>
                  <span className="tag">{r.kind}</span>
                </td>
                <td>{r.chapter_number ?? <span className="muted">—</span>}</td>
                <td>{r.score.toFixed(2)}</td>
                <td>{r.snippet ?? <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
