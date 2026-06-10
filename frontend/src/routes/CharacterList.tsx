import { useQuery } from "@tanstack/react-query";
import { DataSet } from "vis-data";
import { Network } from "vis-network/standalone";
import { useEffect, useRef } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function CharacterList() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const location = useLocation();
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);

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

  useEffect(() => {
    if (!graphData || !containerRef.current) return;
    const nodes = new DataSet(
      graphData.nodes.map((n) => ({ id: n.id, label: n.label, title: n.description ?? undefined }))
    );
    const edges = new DataSet(
      graphData.edges.map((e) => ({
        id: e.id,
        from: e.from,
        to: e.to,
        label: e.label ?? undefined,
        arrows: "to",
      }))
    );
    const network = new Network(
      containerRef.current,
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
    network.on("doubleClick", (params: any) => {
      if (params.nodes.length > 0) {
        navigate(`/novels/${novelId}/characters/${params.nodes[0]}${window.location.search}`);
      }
    });
    networkRef.current = network;
    return () => {
      network.destroy();
      networkRef.current = null;
    };
  }, [graphData, navigate, novelId]);

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
          <div ref={containerRef} className="graph-container" />
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
