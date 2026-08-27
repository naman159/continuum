import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import type { CharacterRelationshipRow } from "../api";
import { api, type CharacterDetail as Detail } from "../api";
import FieldList from "../components/FieldList";
import { renderArray } from "../components/fieldRenderers";
import { useChapterCap } from "../hooks/useChapterCap";

type RelGroup = {
  key: string;
  direction: string;
  entity: string;
  entityType: string;
  rows: CharacterRelationshipRow[];
};

function groupRelationships(rels: CharacterRelationshipRow[]): RelGroup[] {
  const map = new Map<string, RelGroup>();
  for (const r of rels) {
    const key = `${r.direction}::${r.other_entity_name}::${r.other_entity_type}`;
    if (!map.has(key)) {
      map.set(key, { key, direction: r.direction, entity: r.other_entity_name, entityType: r.other_entity_type, rows: [] });
    }
    map.get(key)!.rows.push(r);
  }
  return Array.from(map.values());
}

export default function CharacterDetail() {
  const { novelId, characterId } = useParams();
  const [cap] = useChapterCap();
  const { data, isLoading, error } = useQuery({
    queryKey: ["character", novelId, characterId, cap],
    queryFn: () => api.character(novelId!, characterId!, cap),
    enabled: Boolean(novelId && characterId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data) return <p>Not found.</p>;
  return <CharacterPage data={data} />;
}

function CharacterPage({ data }: { data: Detail }) {
  return (
    <article>
      <h1>{data.identity.name}</h1>
      <section>
        <h2>Identity</h2>
        <FieldList
          fields={[
            { label: "ID", value: <code>{data.identity.id}</code> },
            { label: "Aliases", value: renderArray(data.identity.aliases) },
            { label: "First appearance", value: data.identity.first_appearance_chapter ?? null },
            { label: "Description", value: data.identity.description },
          ]}
        />
      </section>
      <section>
        <h2>Current state</h2>
        {data.current_state == null ? (
          <p className="muted">No state recorded within cap.</p>
        ) : (
          <StateBlock state={data.current_state} />
        )}
      </section>
      <section>
        <h2>State history ({data.history.length})</h2>
        {data.history.map((s, i) => (
          <details key={i} open={i === data.history.length - 1}>
            <summary>Chapter {s.chapter_number}</summary>
            <StateBlock state={s} />
          </details>
        ))}
      </section>
      <section>
        <h2>Relationships ({data.relationships.length})</h2>
        {data.relationships.length === 0 ? (
          <p className="muted">None within cap.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Direction</th>
                <th>Entity</th>
                <th>Type</th>
                <th>From ch.</th>
                <th>To ch.</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {groupRelationships(data.relationships).map(({ key, direction, entity, rows }) => {
                const allSymmetric = rows.every((r) => r.symmetric);
                return rows.map((r, ri) => (
                  <tr key={`${key}-${ri}`}>
                    {ri === 0 && (
                      <>
                        <td rowSpan={rows.length}>{allSymmetric ? "↔" : direction}</td>
                        <td rowSpan={rows.length}>{entity}</td>
                      </>
                    )}
                    <td>{r.rel_type ?? "—"}</td>
                    <td>{r.from_chapter ?? "—"}</td>
                    <td>{r.to_chapter ?? "ongoing"}</td>
                    <td>{r.notes ?? "—"}</td>
                  </tr>
                ));
              })}
            </tbody>
          </table>
        )}
      </section>
      <section>
        <h2>Shared Dynamics ({(data.dynamics ?? []).length})</h2>
        {(data.dynamics ?? []).length === 0 ? (
          <p className="muted">No dynamics recorded within cap.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Chapter</th>
                <th>With</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {(data.dynamics ?? []).map((d) => (
                <tr key={d.id}>
                  <td>{d.chapter_number}</td>
                  <td>{d.other_entity_name} <span className="muted">({d.other_entity_type})</span></td>
                  <td>{d.description ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section>
        <h2>Events involving ({data.events.length})</h2>
        {data.events.length === 0 ? (
          <p className="muted">None within cap.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Chapter</th>
                <th>Description</th>
                <th>Type</th>
                <th>Impact</th>
                <th>With</th>
              </tr>
            </thead>
            <tbody>
              {data.events.map((e) => (
                <tr key={e.id}>
                  <td>{e.chapter_number}</td>
                  <td>{e.description}</td>
                  <td>{e.event_type ?? "—"}</td>
                  <td>{e.impact_level ?? "—"}</td>
                  <td>{e.involved_characters.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </article>
  );
}

function StateBlock({ state }: { state: Detail["history"][number] }) {
  return (
    <FieldList
      fields={[
        { label: "Chapter", value: state.chapter_number },
        { label: "Location", value: state.location },
        { label: "Emotional state", value: state.emotional_state },
        { label: "Goals", value: state.goals },
        { label: "Knowledge", value: renderArray(state.knowledge) },
        { label: "Physical state", value: state.physical_state },
        { label: "Appearance", value: state.appearance },
        { label: "Notes", value: state.notes },
      ]}
    />
  );
}
