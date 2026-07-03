import { useQuery } from "@tanstack/react-query";
import { DataSet } from "vis-data";
import { Network } from "vis-network/standalone";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

// AAA-accessible node palette. Bright, high-luminance hues (drawn from the
// design-system *-text tokens) so each fill clears WCAG non-text contrast
// (≥3:1) against the dark #12141f canvas while staying mutually distinct.
const TYPE_COLORS: Record<string, string> = {
  character: "#60a5fa", // blue-text
  location: "#34d399", // green-text
  object: "#fbbf24", // amber-text
  faction: "#c084fc", // accent-text
};
const CUSTOM_COLOR = "#22d3ee"; // bright cyan

function nodeColor(entityType: string): string {
  return TYPE_COLORS[entityType] ?? CUSTOM_COLOR;
}

function navUrl(novelId: string, entityType: string, id: string, nativeId: string): string {
  switch (entityType) {
    case "character": return `/novels/${novelId}/characters/${nativeId}`;
    case "location":  return `/novels/${novelId}/locations/${nativeId}`;
    case "object":    return `/novels/${novelId}/objects/${nativeId}`;
    case "faction":   return `/novels/${novelId}/factions/${nativeId}`;
    default:          return `/novels/${novelId}/custom-entities/${id}`;
  }
}

const TYPE_LABELS: Record<string, string> = {
  character: "Characters",
  location: "Locations",
  object: "Objects",
  faction: "Factions",
};

function typeLabel(t: string): string {
  return TYPE_LABELS[t] ?? (t.charAt(0).toUpperCase() + t.slice(1) + "s");
}

export default function EntityGraph() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  const nodesRef = useRef<DataSet<any> | null>(null);
  const edgesRef = useRef<DataSet<any> | null>(null);
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());

  const { data, isLoading } = useQuery({
    queryKey: ["entity-graph", novelId, cap],
    queryFn: () => api.entityGraph(novelId!, cap),
    enabled: Boolean(novelId),
  });

  // Build the network once per dataset. Legend toggles only flip `hidden`
  // flags below — destroying and re-stabilizing the whole graph on every
  // toggle would freeze the UI and discard the layout the user is looking at.
  useEffect(() => {
    if (!data || !containerRef.current) return;

    const nodes = new DataSet(
      data.nodes.map((n) => ({
        id: n.id,
        label: n.label,
        title: n.description ?? undefined,
        color: nodeColor(n.entity_type),
      }))
    );
    const edges = new DataSet(
      data.edges.map((e) => ({
        id: e.id,
        from: e.from,
        to: e.to,
        label: e.edge_kind === "relationship" ? (e.label ?? undefined) : undefined,
        title: e.tooltip ?? undefined,
        arrows: e.edge_kind === "relationship" ? "to" : undefined,
        dashes: e.edge_kind !== "relationship",
        color: e.edge_kind === "relationship"
          ? { color: "#60a5fa", opacity: 1 }
          : { color: "#8c89a6", opacity: 0.9 },
        width: e.edge_kind === "relationship" ? 2 : 1,
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
          // Light label text with a dark halo so names stay readable (AAA,
          // ~13:1 on the canvas) even when they overlap bright nodes/edges.
          font: { size: 14, color: "#e2ddef", strokeWidth: 3, strokeColor: "#0b0d14" },
        },
        edges: {
          font: { size: 11, align: "middle", color: "#e2ddef", strokeWidth: 3, strokeColor: "#0b0d14" },
          smooth: { enabled: true, type: "continuous", roundness: 0.5 },
        },
      }
    );

    network.on("doubleClick", (params: any) => {
      if (params.nodes.length > 0) {
        const nodeId = params.nodes[0] as string;
        const node = data.nodes.find((n) => n.id === nodeId);
        if (node) {
          navigate(
            navUrl(novelId!, node.entity_type, node.id, node.native_id) +
              window.location.search
          );
        }
      }
    });

    networkRef.current = network;
    nodesRef.current = nodes;
    edgesRef.current = edges;
    return () => {
      network.destroy();
      networkRef.current = null;
      nodesRef.current = null;
      edgesRef.current = null;
    };
  }, [data, navigate, novelId]);

  // Apply legend visibility as DataSet deltas; vis-network hides the items
  // in place without a full re-layout.
  useEffect(() => {
    const nodes = nodesRef.current;
    const edges = edgesRef.current;
    if (!data || !nodes || !edges) return;
    const typeById = new Map(data.nodes.map((n) => [n.id, n.entity_type]));
    nodes.update(
      data.nodes.map((n) => ({ id: n.id, hidden: hiddenTypes.has(n.entity_type) }))
    );
    edges.update(
      data.edges.map((e) => ({
        id: e.id,
        hidden:
          hiddenTypes.has(typeById.get(e.from) ?? "") ||
          hiddenTypes.has(typeById.get(e.to) ?? ""),
      }))
    );
  }, [data, hiddenTypes]);

  function toggleType(entityType: string) {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(entityType)) next.delete(entityType);
      else next.add(entityType);
      return next;
    });
  }

  if (isLoading) return <p>Loading…</p>;
  if (!data) return null;

  const presentTypes = [...new Set(data.nodes.map((n) => n.entity_type))].sort();

  return (
    <div>
      <h1>Entity Graph</h1>
      {presentTypes.length > 0 && (
        <div className="entity-graph-legend">
          {presentTypes.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => toggleType(t)}
              className={`entity-type-pill${hiddenTypes.has(t) ? " dimmed" : ""}`}
              aria-pressed={!hiddenTypes.has(t)}
            >
              <span className="pill-dot" style={{ background: nodeColor(t) }} />
              {typeLabel(t)}
            </button>
          ))}
        </div>
      )}
      {data.edges.length > 0 ? (
        <>
          <p className="graph-caption">Double-click a node to open · Hover for description</p>
          <div ref={containerRef} className="graph-container" />
          <p className="graph-caption">{data.nodes.length} nodes · {data.edges.length} edges</p>
        </>
      ) : (
        <p>No relationships yet. Process some chapters to populate the graph.</p>
      )}
    </div>
  );
}
