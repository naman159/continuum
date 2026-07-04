import { useQuery } from "@tanstack/react-query";
import { DataSet } from "vis-data";
import { Network } from "vis-network/standalone";
import { useEffect, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Relationships() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["graph", novelId, cap],
    queryFn: () => api.relationships(novelId!, cap),
    enabled: Boolean(novelId),
  });

  useEffect(() => {
    if (!data || !containerRef.current) return;
    const nodes = new DataSet(
      data.nodes.map((n) => ({ id: n.id, label: n.label, title: n.description ?? undefined }))
    );
    const edges = new DataSet(
      data.edges.map((e) => ({
        id: e.id,
        from: e.from,
        to: e.to,
        label: e.label ?? undefined,
        arrows: e.symmetric ? undefined : "to",
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
  }, [data, navigate, novelId]);

  if (isLoading) return <p>Loading…</p>;
  return (
    <div>
      <h1>Relationships</h1>
      <p className="graph-caption">Double-click a node to open a character. Hover for description.</p>
      <div ref={containerRef} style={{ height: 600, border: "1px solid #ddd" }} />
      <h2>All relationships ({data?.edges.length ?? 0})</h2>
      <table>
        <thead>
          <tr><th>From</th><th>To</th><th>Type</th><th>From ch.</th></tr>
        </thead>
        <tbody>
          {data?.edges.map((e) => {
            const fromName = data.nodes.find((n) => n.id === e.from)?.label ?? e.from;
            const toName = data.nodes.find((n) => n.id === e.to)?.label ?? e.to;
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
    </div>
  );
}
