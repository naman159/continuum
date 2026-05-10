import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Novel } from "../api";

async function createNovel(title: string, author: string, language: string): Promise<Novel> {
  const res = await fetch("/api/novels", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title,
      author: author.trim() || null,
      language: language.trim() || null,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export default function Novels() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [language, setLanguage] = useState("");

  const { data, isLoading, error } = useQuery({ queryKey: ["novels"], queryFn: api.novels });

  const mutation = useMutation({
    mutationFn: () => createNovel(title, author, language),
    onSuccess: (novel) => {
      queryClient.invalidateQueries({ queryKey: ["novels"] });
      navigate(`/novels/${novel.id}/process`);
    },
  });

  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;

  return (
    <div>
      <h1>Novels</h1>

      {!showForm && (
        <button onClick={() => setShowForm(true)}>New Novel</button>
      )}

      {showForm && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!title.trim()) return;
            mutation.mutate();
          }}
          style={{ marginBottom: 16 }}
        >
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-title">Title</label>
            <br />
            <input
              id="novel-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              style={{ marginTop: 4 }}
            />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-author">Author (optional)</label>
            <br />
            <input
              id="novel-author"
              type="text"
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              style={{ marginTop: 4 }}
            />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-language">Language (optional)</label>
            <br />
            <input
              id="novel-language"
              type="text"
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              style={{ marginTop: 4 }}
            />
          </div>
          {mutation.isError && (
            <p style={{ color: "red" }}>Error: {(mutation.error as Error).message}</p>
          )}
          <button type="submit" disabled={mutation.isPending || !title.trim()}>
            {mutation.isPending ? "Creating…" : "Create Novel"}
          </button>
          {" "}
          <button type="button" onClick={() => setShowForm(false)} disabled={mutation.isPending}>
            Cancel
          </button>
        </form>
      )}

      {(!data || data.length === 0) && !showForm && <p>No novels.</p>}
      {data && data.length > 0 && (
        <ul>
          {data.map((n) => (
            <li key={n.id}>
              <Link to={`/novels/${n.id}/characters`}>{n.title}</Link>
              {" — "}
              {n.author ?? "Unknown"} · {n.max_chapter} chapter{n.max_chapter === 1 ? "" : "s"}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
