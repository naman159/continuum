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

function BookIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
      <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function ChaptersIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M4 6h16M4 12h16M4 18h7" />
    </svg>
  );
}

function AuthorIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
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

  if (isLoading) return <p className="muted" style={{ padding: "20px 0" }}>Loading…</p>;
  if (error) return <p style={{ color: "var(--red-text)", padding: "20px 0" }}>Error: {String(error instanceof Error ? error.message : error)}</p>;

  return (
    <div>
      <div className="page-header">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h1 style={{ margin: 0 }}>Library</h1>
        </div>
        {!showForm && (
          <button className="btn-primary" onClick={() => setShowForm(true)}>
            <PlusIcon />
            New Novel
          </button>
        )}
      </div>

      {showForm && step === "details" && (
        <div className="form-panel">
          <p className="form-panel-title">New Novel</p>
          <form onSubmit={(e) => { e.preventDefault(); if (!title.trim()) return; setStep("types"); }}>
            <div className="form-group">
              <label className="form-label" htmlFor="novel-title">Title</label>
              <input
                id="novel-title"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="The Name of the Wind"
                required
                autoFocus
              />
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="novel-author">Author <span style={{ color: "var(--text-muted)", fontWeight: 400, textTransform: "none" }}>(optional)</span></label>
              <input
                id="novel-author"
                type="text"
                value={author}
                onChange={(e) => setAuthor(e.target.value)}
                placeholder="Patrick Rothfuss"
              />
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="novel-language">Language <span style={{ color: "var(--text-muted)", fontWeight: 400, textTransform: "none" }}>(optional)</span></label>
              <input
                id="novel-language"
                type="text"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                placeholder="English"
              />
            </div>
            <div className="form-actions">
              <button type="submit" className="btn-primary" disabled={!title.trim()}>
                Next: Entity Types →
              </button>
              <button type="button" className="btn-ghost" onClick={resetForm}>Cancel</button>
            </div>
          </form>
        </div>
      )}

      {showForm && step === "types" && (
        <div className="form-panel">
          <p className="form-panel-title">Custom Entity Types</p>
          <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 16, lineHeight: 1.6 }}>
            Beyond Characters, Locations, Factions, and Objects — add types that matter to this story.
          </p>

          {genres && genres.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <label className="form-label">Genre presets</label>
              <div className="preset-pills">
                {genres.map((g) => (
                  <button key={g.id} type="button" className="preset-pill" onClick={() => applyPreset(g)}>
                    {g.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {customTypes.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <label className="form-label">Selected types</label>
              <ul className="entity-type-list">
                {customTypes.map((t) => (
                  <li key={t.name} className="entity-type-item">
                    {t.name.replace(/_/g, " ")}
                    <button
                      type="button"
                      className="entity-type-remove"
                      aria-label={`Remove ${t.name}`}
                      onClick={() => removeType(t.name)}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
            <input
              type="text"
              placeholder="Add type (e.g. deity, artifact, ship)"
              value={newTypeName}
              onChange={(e) => setNewTypeName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCustomType(); } }}
            />
            <button type="button" onClick={addCustomType} style={{ flexShrink: 0, width: "auto" }}>
              Add
            </button>
          </div>

          {mutation.isError && (
            <p style={{ color: "var(--red-text)", fontSize: 13, marginBottom: 12 }}>
              Error: {String(mutation.error instanceof Error ? mutation.error.message : mutation.error)}
            </p>
          )}

          <div className="form-actions">
            <button type="button" className="btn-primary" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
              {mutation.isPending ? "Creating…" : "Create Novel"}
            </button>
            <button type="button" className="btn-ghost" onClick={() => setStep("details")} disabled={mutation.isPending}>
              ← Back
            </button>
            <button type="button" className="btn-ghost" onClick={resetForm} disabled={mutation.isPending}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {(!data || data.length === 0) && !showForm && (
        <div className="empty-state">
          <div style={{ marginBottom: 16, opacity: 0.3 }}>
            <BookIcon />
          </div>
          <p style={{ marginBottom: 12, fontFamily: "'Crimson Pro', Georgia, serif", fontSize: "1.1rem", color: "var(--text)" }}>
            No novels yet
          </p>
          <p style={{ fontSize: 13 }}>Add your first novel to start tracking characters, events, and plot threads.</p>
        </div>
      )}

      {data && data.length > 0 && (
        <div className="novel-grid">
          {data.map((n) => (
            <Link
              key={n.id}
              to={`/novels/${n.id}/characters`}
              className="novel-card"
            >
              <div className="novel-card-title">{n.title}</div>
              <div className="novel-card-meta">
                {n.author && (
                  <span className="novel-card-stat">
                    <AuthorIcon />
                    {n.author}
                  </span>
                )}
                <span className="novel-card-stat">
                  <ChaptersIcon />
                  {n.max_chapter} ch{n.max_chapter === 1 ? "" : "s"}
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
