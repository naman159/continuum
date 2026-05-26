import { useQuery } from "@tanstack/react-query";
import { DataSet } from "vis-data";
import { Network } from "vis-network/standalone";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

const TYPE_COLORS: Record<string, string> = {
  character: "#4e9af1",
  location: "#52c41a",
  object: "#fa8c16",
  faction: "#9254de",
};
const CUSTOM_COLOR = "#13c2c2";

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
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());

  const { data, isLoading } = useQuery({
    queryKey: ["entity-graph", novelId, cap],
    queryFn: () => api.entityGraph(novelId!, cap),
    enabled: Boolean(novelId),
  });

  useEffect(() => {
    if (!data || !containerRef.current) return;

    const visibleNodes = data.nodes.filter((n) => !hiddenTypes.has(n.entity_type));
    const visibleIds = new Set(visibleNodes.map((n) => n.id));
    const visibleEdges = data.edges.filter(
      (e) => visibleIds.has(e.from) && visibleIds.has(e.to)
    );

    const nodes = new DataSet(
      visibleNodes.map((n) => ({
        id: n.id,
        label: n.label,
        title: n.description ?? undefined,
        color: nodeColor(n.entity_type),
      }))
    );
    const edges = new DataSet(
      visibleEdges.map((e) => ({
        id: e.id,
        from: e.from,
        to: e.to,
        label: e.edge_kind === "relationship" ? (e.label ?? undefined) : undefined,
        title: e.tooltip ?? undefined,
        arrows: e.edge_kind === "relationship" ? "to" : undefined,
        dashes: e.edge_kind !== "relationship",
        color: e.edge_kind === "relationship"
          ? { color: "#4e9af1", opacity: 1 }
          : { color: "#666", opacity: 0.7 },
        width: e.edge_kind === "relationship" ? 2 : 1,
      }))
    );

    const network = new Network(
      containerRef.current,
      { nodes, edges },
      {
        physics: { stabilization: { iterations: 200 } },
        nodes: { shape: "dot", size: 16, font: { size: 14 } },
        edges: {
          font: { size: 11, align: "middle" },
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
    return () => {
      network.destroy();
      networkRef.current = null;
    };
  }, [data, hiddenTypes, navigate, novelId]);

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
          <p className="muted">Double-click a node to open · Hover for description</p>
          <div ref={containerRef} style={{ height: 600, border: "1px solid #ddd" }} />
          <p className="muted">{data.nodes.length} nodes · {data.edges.length} edges</p>
        </>
      ) : (
        <p>No relationships yet. Process some chapters to populate the graph.</p>
      )}
    </div>
  );
}
