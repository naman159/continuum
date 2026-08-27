import { useQuery } from "@tanstack/react-query";
import { DataSet } from "vis-data";
import { Network } from "vis-network/standalone";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function CharacterList() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const location = useLocation();
  const navigate = useNavigate();
  const [graphEl, setGraphEl] = useState<HTMLDivElement | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["characters", novelId, cap],
    queryFn: () => api.characters(novelId!, cap),
    enabled: Boolean(novelId),
  });

  const { data: graphData, isLoading: graphLoading } = useQuery({
    queryKey: ["graph", novelId, cap],
    queryFn: () => api.relationships(novelId!, cap),
    enabled: Boolean(novelId),
  });

  // A callback ref, not useRef: the isLoading early return below means the
  // graph container is not mounted while the character list is still
  // loading. If the graph query resolved first, the effect ran, bailed on a
  // null ref, and never re-ran — graphData kept the same reference — so the
  // graph silently never rendered. Storing the element in state re-runs the
  // effect at the moment the container mounts.
  useEffect(() => {
    if (!graphData || !graphEl) return;
    const nodes = new DataSet(
      graphData.nodes.map((n) => ({ id: n.id, label: n.label, title: n.description ?? undefined }))
    );
    const edges = new DataSet(
      graphData.edges.map((e) => ({
        id: e.id,
        from: e.from,
        to: e.to,
        label: e.label ?? undefined,
        // Mutual relations (spouse_of and friends) must not be drawn with a
        // direction; Relationships.tsx already does this and both pages read
        // the same cache entry, so an arrow here contradicted that page.
        arrows: e.symmetric ? undefined : "to",
      }))
    );
    const network = new Network(
      graphEl,
      { nodes, edges },
      {
        physics: { stabilization: { iterations: 200 } },
        nodes: {
          shape: "dot",
          size: 16,
          borderWidth: 2,
          color: { background: "#60a5fa", border: "#1f2235" },
          // Light label text + dark halo for AAA legibility on the dark canvas.
          font: { size: 14, color: "#e2ddef", strokeWidth: 3, strokeColor: "#0b0d14" },
        },
        edges: {
          font: { size: 11, align: "middle", color: "#e2ddef", strokeWidth: 3, strokeColor: "#0b0d14" },
          color: { color: "#8c89a6", highlight: "#60a5fa" },
          smooth: { enabled: true, type: "continuous", roundness: 0.5 },
        },
      }
    );
    network.on("doubleClick", (params: { nodes: string[] }) => {
      if (params.nodes.length > 0) {
        navigate(`/novels/${novelId}/characters/${params.nodes[0]}${window.location.search}`);
      }
    });
    return () => {
      network.destroy();
    };
  }, [graphData, graphEl, navigate, novelId]);

  if (isLoading) return <p className="muted">Loading…</p>;
  if (error) return <p style={{ color: "var(--red-text)" }}>Error: {(error as Error).message}</p>;
  if (!data || data.length === 0) return <div className="empty-state"><p>No characters. Process a chapter to extract them.</p></div>;

  return (
    <div>
      <h1>Characters</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Aliases</th>
            <th>First seen</th>
          </tr>
        </thead>
        <tbody>
          {data.map((c) => (
            <tr key={c.id}>
              <td>
                <Link to={`/novels/${novelId}/characters/${c.id}${location.search}`}>{c.name}</Link>
              </td>
              <td>{c.aliases.join(", ") || "—"}</td>
              <td>{c.first_appearance_chapter ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Relationships</h2>
      {graphLoading ? (
        <p>Loading graph…</p>
      ) : (
        <>
          <p className="graph-caption">Double-click a node to open an entity. Hover for description.</p>
          <div ref={setGraphEl} className="graph-container" />
          <h3>All relationships ({graphData?.edges.length ?? 0})</h3>
          <table>
            <thead>
              <tr><th>From</th><th>To</th><th>Type</th><th>From ch.</th></tr>
            </thead>
            <tbody>
              {graphData?.edges.map((e) => {
                const fromName = graphData.nodes.find((n) => n.id === e.from)?.label ?? e.from;
                const toName = graphData.nodes.find((n) => n.id === e.to)?.label ?? e.to;
                return (
                  <tr key={e.id}>
                    <td>{fromName}</td>
                    <td>{toName}</td>
                    <td>{e.label ?? "—"}</td>
                    <td>{e.chapter_number ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
