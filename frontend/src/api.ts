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
  involved_factions: string[];
};

export type CharacterRelationshipRow = {
  other_entity_name: string;
  other_entity_type: string;
  direction: "from" | "to";
  rel_type: string | null;
  from_chapter: number | null;
  to_chapter: number | null;
  notes: string | null;
};

export type SharedDynamicRow = {
  id: string;
  entity_a_name: string;
  entity_b_name: string;
  chapter_number: number;
  description: string | null;
};

export type CharacterDynamicRow = {
  id: string;
  chapter_number: number;
  other_entity_name: string;
  other_entity_type: string;
  description: string | null;
};

export type CharacterDetail = {
  identity: CharacterSummary;
  current_state: CharacterStateRow | null;
  history: CharacterStateRow[];
  relationships: CharacterRelationshipRow[];
  dynamics: CharacterDynamicRow[];
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

export type LocationSummary = {
  id: string;
  name: string;
  aliases: string[];
  description: string | null;
  first_appearance_chapter: number | null;
};

export type LocationDetail = {
  identity: LocationSummary;
  events: TimelineEvent[];
  characters: string[];
};

export type ObjectSummary = {
  id: string;
  name: string;
  aliases: string[];
  description: string | null;
  significance: string | null;
  first_appearance_chapter: number | null;
};

export type ObjectRelationship = {
  character_name: string;
  rel_type: string | null;
  from_chapter: number | null;
  to_chapter: number | null;
  notes: string | null;
};

export type ObjectDetail = {
  identity: ObjectSummary;
  events: TimelineEvent[];
  characters: string[];
  relationships: ObjectRelationship[];
};

export type FactionSummary = {
  id: string;
  name: string;
  aliases: string[];
  description: string | null;
};

export type FactionDetail = {
  identity: FactionSummary;
  events: TimelineEvent[];
  characters: string[];
};

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
  dynamics: (id: string, cap: number | null) =>
    fetchJson<SharedDynamicRow[]>(`/api/novels/${id}/dynamics${capParam(cap)}`),
  locations: (novelId: string, cap: number | null) =>
    fetchJson<LocationSummary[]>(`/api/novels/${novelId}/locations${capParam(cap)}`),
  location: (novelId: string, locationId: string, cap: number | null) =>
    fetchJson<LocationDetail>(`/api/novels/${novelId}/locations/${locationId}${capParam(cap)}`),
  objects: (novelId: string, cap: number | null) =>
    fetchJson<ObjectSummary[]>(`/api/novels/${novelId}/objects${capParam(cap)}`),
  object: (novelId: string, objectId: string, cap: number | null) =>
    fetchJson<ObjectDetail>(`/api/novels/${novelId}/objects/${objectId}${capParam(cap)}`),
  factions: (novelId: string) =>
    fetchJson<FactionSummary[]>(`/api/novels/${novelId}/factions`),
  faction: (novelId: string, factionId: string) =>
    fetchJson<FactionDetail>(`/api/novels/${novelId}/factions/${factionId}`),
};
