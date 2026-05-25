import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

type Tab = "knows" | "locations" | "possessions";

export default function Knowledge() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const [tab, setTab] = useState<Tab>("knows");
  const [onlyActive, setOnlyActive] = useState(true);

  return (
    <div>
      <h1>State & Knowledge</h1>
      <p className="muted">
        Theory-of-mind tracking (who knows what), bitemporal location edges,
        and possession edges. New continuity primitives — the largest source
        of LLM continuity errors when not tracked externally.
      </p>

      <nav style={{ marginBottom: 16 }}>
        {(["knows", "locations", "possessions"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              marginRight: 8,
              fontWeight: tab === t ? 700 : 400,
              textDecoration: tab === t ? "underline" : "none",
            }}
          >
            {t === "knows" ? "Who knows what" : t === "locations" ? "Location history" : "Possessions"}
          </button>
        ))}
      </nav>

      {tab === "knows" && <KnowsTable novelId={novelId!} cap={cap} />}
      {tab === "locations" && (
        <LocationEdgesTable
          novelId={novelId!}
          cap={cap}
          onlyActive={onlyActive}
          setOnlyActive={setOnlyActive}
        />
      )}
      {tab === "possessions" && (
        <PossessionEdgesTable
          novelId={novelId!}
          cap={cap}
          onlyActive={onlyActive}
          setOnlyActive={setOnlyActive}
        />
      )}
    </div>
  );
}

function KnowsTable({ novelId, cap }: { novelId: string; cap: number | null }) {
  const { data, isLoading } = useQuery({
    queryKey: ["knows", novelId, cap],
    queryFn: () => api.knows(novelId, cap, null),
  });
  if (isLoading) return <p>Loading…</p>;
  if (!data || data.length === 0) return <p className="muted">No knowledge edges yet.</p>;
  // Group by character.
  const byChar = new Map<string, typeof data>();
  data.forEach((k) => {
    const arr = byChar.get(k.character_name) ?? [];
    arr.push(k);
    byChar.set(k.character_name, arr);
  });
  return (
    <>
      {Array.from(byChar.entries()).map(([name, edges]) => (
        <section key={name} style={{ marginTop: 16 }}>
          <h2>{name}</h2>
          <table>
            <thead>
              <tr>
                <th>Learned ch</th>
                <th>Fact</th>
                <th>Source</th>
                <th>Certainty</th>
                <th>Shared with</th>
              </tr>
            </thead>
            <tbody>
              {edges!.map((k) => (
                <tr key={k.id}>
                  <td>{k.learned_chapter}</td>
                  <td>{k.fact_description}</td>
                  <td><span className="muted">{k.source_type ?? "—"}</span></td>
                  <td>{k.certainty?.toFixed(2) ?? "—"}</td>
                  <td>{k.shared_with_names.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </>
  );
}

function LocationEdgesTable({
  novelId,
  cap,
  onlyActive,
  setOnlyActive,
}: {
  novelId: string;
  cap: number | null;
  onlyActive: boolean;
  setOnlyActive: (v: boolean) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["loc-history", novelId, cap, onlyActive],
    queryFn: () => api.locationsHistory(novelId, cap, onlyActive),
  });
  if (isLoading) return <p>Loading…</p>;
  return (
    <>
      <label>
        <input
          type="checkbox"
          checked={onlyActive}
          onChange={(e) => setOnlyActive(e.target.checked)}
        />{" "}
        Only currently-active edges
      </label>
      {(!data || data.length === 0) && (
        <p className="muted">No location edges yet.</p>
      )}
      <table style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th>Entity</th>
            <th>Type</th>
            <th>Location</th>
            <th>Since ch</th>
            <th>Until ch</th>
            <th>Certainty</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((e) => (
            <tr key={e.id}>
              <td>{e.entity_name ?? "—"}</td>
              <td><span className="muted">{e.entity_type ?? "—"}</span></td>
              <td>{e.location_name ?? "—"}</td>
              <td>{e.since_chapter}</td>
              <td>{e.until_chapter ?? <span className="muted">(open)</span>}</td>
              <td>{e.certainty?.toFixed(2) ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function PossessionEdgesTable({
  novelId,
  cap,
  onlyActive,
  setOnlyActive,
}: {
  novelId: string;
  cap: number | null;
  onlyActive: boolean;
  setOnlyActive: (v: boolean) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["possessions", novelId, cap, onlyActive],
    queryFn: () => api.possessions(novelId, cap, onlyActive),
  });
  if (isLoading) return <p>Loading…</p>;
  return (
    <>
      <label>
        <input
          type="checkbox"
          checked={onlyActive}
          onChange={(e) => setOnlyActive(e.target.checked)}
        />{" "}
        Only currently-held items
      </label>
      {(!data || data.length === 0) && (
        <p className="muted">No possession edges yet.</p>
      )}
      <table style={{ marginTop: 12 }}>
        <thead>
          <tr>
            <th>Character</th>
            <th>Object</th>
            <th>Since ch</th>
            <th>Until ch</th>
            <th>Certainty</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((p) => (
            <tr key={p.id}>
              <td>{p.character_name ?? "—"}</td>
              <td>{p.object_name ?? "—"}</td>
              <td>{p.since_chapter}</td>
              <td>{p.until_chapter ?? <span className="muted">(held)</span>}</td>
              <td>{p.certainty?.toFixed(2) ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
