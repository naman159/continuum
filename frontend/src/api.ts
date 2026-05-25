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
  entity_type: string | null;
};

export type CharacterStateRow = {
  chapter_number: number;
  location: string | null;
  emotional_state: string | null;
  goals: string | null;
  knowledge: string[];
  physical_state: string | null;
  appearance: string | null;
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
  summary_short: string | null;
  summary_long: string | null;
  processed_at: string | null;
};

export type SceneRow = {
  id: string;
  chapter_id: string;
  chapter_number: number;
  scene_index: number;
  pov_character_id: string | null;
  pov_character_name: string | null;
  location_id: string | null;
  location_name: string | null;
  time_anchor: string | null;
  story_time_ordinal: number | null;
  present_character_names: string[];
  summary: string | null;
};

export type CommitmentRow = {
  id: string;
  foreshadow_text: string;
  foreshadow_chapter: number;
  payoff_text: string | null;
  payoff_chapter: number | null;
  trigger_predicate: Record<string, unknown> | null;
  status: "pending" | "satisfied" | "broken" | "abandoned" | string;
  weight: number | null;
  related_entity_names: string[];
  age_chapters: number | null;
};

export type CanonFactRow = {
  id: string;
  kind: string;
  subject_entity_id: string | null;
  subject_name: string | null;
  predicate: string;
  value: string;
  source_chapter: number | null;
  confidence: number | null;
  locked: boolean;
};

export type KnowsEdgeRow = {
  id: string;
  character_id: string;
  character_name: string;
  fact_description: string;
  learned_chapter: number;
  source_type: string | null;
  source_event_id: string | null;
  certainty: number | null;
  shared_with_names: string[];
};

export type LocationEdgeRow = {
  id: string;
  entity_id: string;
  entity_name: string | null;
  entity_type: string | null;
  location_id: string;
  location_name: string | null;
  since_chapter: number;
  until_chapter: number | null;
  certainty: number | null;
};

export type PossessionEdgeRow = {
  id: string;
  character_id: string;
  character_name: string | null;
  object_id: string;
  object_name: string | null;
  since_chapter: number;
  until_chapter: number | null;
  certainty: number | null;
};


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
  events: CharacterEventRow[];
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
  events: CharacterEventRow[];
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
  events: CharacterEventRow[];
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
    fetchJson<CharacterEventRow[]>(`/api/novels/${id}/timeline${capParam(cap)}`),
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
  scenes: (novelId: string, cap: number | null, chapter: number | null) => {
    const params = new URLSearchParams();
    if (cap != null) params.set("cap", String(cap));
    if (chapter != null) params.set("chapter", String(chapter));
    const qs = params.toString();
    return fetchJson<SceneRow[]>(`/api/novels/${novelId}/scenes${qs ? `?${qs}` : ""}`);
  },
  commitments: (novelId: string, cap: number | null, status: string) => {
    const params = new URLSearchParams();
    if (cap != null) params.set("cap", String(cap));
    params.set("status", status);
    return fetchJson<CommitmentRow[]>(`/api/novels/${novelId}/commitments?${params}`);
  },
  canon: (novelId: string, lockedOnly: boolean) => {
    const params = new URLSearchParams();
    if (lockedOnly) params.set("locked_only", "true");
    const qs = params.toString();
    return fetchJson<CanonFactRow[]>(`/api/novels/${novelId}/canon${qs ? `?${qs}` : ""}`);
  },
  knows: (novelId: string, cap: number | null, characterId: string | null) => {
    const params = new URLSearchParams();
    if (cap != null) params.set("cap", String(cap));
    if (characterId) params.set("character_id", characterId);
    const qs = params.toString();
    return fetchJson<KnowsEdgeRow[]>(`/api/novels/${novelId}/knows${qs ? `?${qs}` : ""}`);
  },
  locationsHistory: (novelId: string, cap: number | null, onlyActive: boolean) => {
    const params = new URLSearchParams();
    if (cap != null) params.set("cap", String(cap));
    if (onlyActive) params.set("only_active", "true");
    const qs = params.toString();
    return fetchJson<LocationEdgeRow[]>(
      `/api/novels/${novelId}/locations-history${qs ? `?${qs}` : ""}`
    );
  },
  possessions: (novelId: string, cap: number | null, onlyActive: boolean) => {
    const params = new URLSearchParams();
    if (cap != null) params.set("cap", String(cap));
    if (onlyActive) params.set("only_active", "true");
    const qs = params.toString();
    return fetchJson<PossessionEdgeRow[]>(
      `/api/novels/${novelId}/possessions${qs ? `?${qs}` : ""}`
    );
  },
};
