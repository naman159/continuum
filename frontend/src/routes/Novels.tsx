import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Novel, GenrePreset } from "../api";

type EntityTypeInput = { name: string; description: string };

async function createNovel(
  title: string,
  author: string,
  language: string,
  customEntityTypes: EntityTypeInput[]
): Promise<Novel> {
  const res = await fetch("/api/novels", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title,
      author: author.trim() || null,
      language: language.trim() || null,
      custom_entity_types: customEntityTypes,
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
  const [step, setStep] = useState<"details" | "types">("details");
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [language, setLanguage] = useState("");
  const [customTypes, setCustomTypes] = useState<EntityTypeInput[]>([]);
  const [newTypeName, setNewTypeName] = useState("");

  const { data, isLoading, error } = useQuery({ queryKey: ["novels"], queryFn: api.novels });
  const { data: genres } = useQuery({ queryKey: ["genres"], queryFn: api.genres });

  const mutation = useMutation({
    mutationFn: () => createNovel(title, author, language, customTypes),
    onSuccess: (novel) => {
      queryClient.invalidateQueries({ queryKey: ["novels"] });
      navigate(`/novels/${novel.id}/process`);
    },
  });

  function applyPreset(preset: GenrePreset) {
    const existing = new Set(customTypes.map((t) => t.name));
    const toAdd = preset.types.filter((t) => !existing.has(t.name));
    setCustomTypes((prev) => [...prev, ...toAdd]);
  }

  function removeType(name: string) {
    setCustomTypes((prev) => prev.filter((t) => t.name !== name));
  }

  function addCustomType() {
    const trimmed = newTypeName.trim().toLowerCase().replace(/\s+/g, "_");
    if (!trimmed || customTypes.some((t) => t.name === trimmed)) return;
    setCustomTypes((prev) => [...prev, { name: trimmed, description: "" }]);
    setNewTypeName("");
  }

  function resetForm() {
    setTitle(""); setAuthor(""); setLanguage("");
    setCustomTypes([]); setNewTypeName("");
    setStep("details"); setShowForm(false);
  }

  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;

  return (
    <div>
      <h1>Novels</h1>

      {!showForm && (
        <button onClick={() => setShowForm(true)}>New Novel</button>
      )}

      {showForm && step === "details" && (
        <form onSubmit={(e) => { e.preventDefault(); if (!title.trim()) return; setStep("types"); }} style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-title">Title</label><br />
            <input id="novel-title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} required style={{ marginTop: 4 }} />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-author">Author (optional)</label><br />
            <input id="novel-author" type="text" value={author} onChange={(e) => setAuthor(e.target.value)} style={{ marginTop: 4 }} />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label htmlFor="novel-language">Language (optional)</label><br />
            <input id="novel-language" type="text" value={language} onChange={(e) => setLanguage(e.target.value)} style={{ marginTop: 4 }} />
          </div>
          <button type="submit" disabled={!title.trim()}>Next: Entity Types →</button>
          {" "}
          <button type="button" onClick={resetForm}>Cancel</button>
        </form>
      )}

      {showForm && step === "types" && (
        <div style={{ marginBottom: 16 }}>
          <h3>Custom Entity Types (optional)</h3>
          <p style={{ color: "#666", fontSize: 14 }}>
            Define types beyond Characters, Locations, Factions, and Objects.
          </p>

          {genres && genres.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <strong>Genre presets:</strong>
              {" "}
              {genres.map((g) => (
                <button key={g.id} type="button" onClick={() => applyPreset(g)} style={{ marginRight: 6, marginBottom: 4 }}>
                  {g.label}
                </button>
              ))}
            </div>
          )}

          {customTypes.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <strong>Selected types:</strong>
              <ul style={{ margin: "4px 0", paddingLeft: 20 }}>
                {customTypes.map((t) => (
                  <li key={t.name}>
                    <code>{t.name}</code>
                    {t.description && <span style={{ color: "#666", fontSize: 13 }}> — {t.description}</span>}
                    {" "}
                    <button type="button" onClick={() => removeType(t.name)} style={{ fontSize: 11 }}>✕</button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div style={{ marginBottom: 12 }}>
            <input
              type="text"
              placeholder="Add custom type (e.g. deity)"
              value={newTypeName}
              onChange={(e) => setNewTypeName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustomType(); } }}
            />
            {" "}
            <button type="button" onClick={addCustomType}>Add</button>
          </div>

          {mutation.isError && (
            <p style={{ color: "red" }}>Error: {(mutation.error as Error).message}</p>
          )}
          <button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mutation.isPending ? "Creating…" : "Create Novel"}
          </button>
          {" "}
          <button type="button" onClick={() => setStep("details")} disabled={mutation.isPending}>← Back</button>
          {" "}
          <button type="button" onClick={resetForm} disabled={mutation.isPending}>Cancel</button>
        </div>
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
