export type Novel = {
  id: string;
  title: string;
  author: string | null;
  language: string | null;
  created_at: string;
  max_chapter: number;
};

export type CharacterSummary = {
  id: string;
  name: string;
  aliases: string[];
  description: string | null;
  first_appearance_chapter: number | null;
};

export type CharacterStateRow = {
  chapter_number: number;
  location: string | null;
  emotional_state: string | null;
  goals: string | null;
  knowledge: string[];
  relationships: Record<string, string>;
  physical_state: string | null;
  notes: string | null;
};

export type CharacterEventRow = {
  id: string;
  chapter_number: number;
  description: string;
  event_type: string | null;
  impact_level: string | null;
  involved_characters: string[];
  involved_locations: string[];
  involved_objects: string[];
};

export type CharacterRelationshipRow = {
  chapter_number: number | null;
  other_character_id: string;
  other_character_name: string;
  direction: "from" | "to";
  rel_type: string | null;
  status: string | null;
  notes: string | null;
};

export type CharacterDetail = {
  identity: CharacterSummary;
  current_state: CharacterStateRow | null;
  history: CharacterStateRow[];
  relationships: CharacterRelationshipRow[];
  events: CharacterEventRow[];
};

export type ChapterSummary = {
  id: string;
  number: number;
  title: string | null;
  summary: string | null;
  processed_at: string | null;
};

export type TimelineEvent = CharacterEventRow;

export type ThreadEventLink = {
  event_id: string;
  description: string;
  chapter_number: number;
  impact: string | null;
};

export type PlotThread = {
  id: string;
  title: string;
  description: string | null;
  status: string;
  thread_type: string | null;
  opened_chapter: number | null;
  closed_chapter: number | null;
  events: ThreadEventLink[];
};

export type ContinuityFlag = {
  id: string;
  chapter_number: number;
  description: string;
  flag_type: string | null;
  resolved: boolean;
  resolved_chapter_number: number | null;
};

export type GraphNode = { id: string; label: string; description: string | null };
export type GraphEdge = { id: string; from: string; to: string; label: string | null; chapter_number: number | null };
export type RelationshipGraph = { nodes: GraphNode[]; edges: GraphEdge[] };

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

const capParam = (cap: number | null) => (cap == null ? "" : `?cap=${cap}`);

export const api = {
  novels: () => fetchJson<Novel[]>("/api/novels"),
  novel: (id: string) => fetchJson<Novel>(`/api/novels/${id}`),
  characters: (id: string, cap: number | null) =>
    fetchJson<CharacterSummary[]>(`/api/novels/${id}/characters${capParam(cap)}`),
  character: (novelId: string, characterId: string, cap: number | null) =>
    fetchJson<CharacterDetail>(`/api/novels/${novelId}/characters/${characterId}${capParam(cap)}`),
  chapters: (id: string, cap: number | null) =>
    fetchJson<ChapterSummary[]>(`/api/novels/${id}/chapters${capParam(cap)}`),
  timeline: (id: string, cap: number | null) =>
    fetchJson<TimelineEvent[]>(`/api/novels/${id}/timeline${capParam(cap)}`),
  threads: (id: string, cap: number | null, status: string) => {
    const params = new URLSearchParams();
    if (cap != null) params.set("cap", String(cap));
    params.set("status", status);
    return fetchJson<PlotThread[]>(`/api/novels/${id}/threads?${params}`);
  },
  continuity: (id: string, cap: number | null, resolved: string) => {
    const params = new URLSearchParams();
    if (cap != null) params.set("cap", String(cap));
    params.set("resolved", resolved);
    return fetchJson<ContinuityFlag[]>(`/api/novels/${id}/continuity?${params}`);
  },
  relationships: (id: string, cap: number | null) =>
    fetchJson<RelationshipGraph>(`/api/novels/${id}/relationships${capParam(cap)}`),
};
