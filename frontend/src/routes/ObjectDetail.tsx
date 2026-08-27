import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";
import FieldList from "../components/FieldList";
import { renderArray } from "../components/fieldRenderers";
import { useChapterCap } from "../hooks/useChapterCap";

export default function ObjectDetail() {
  const { novelId, objectId } = useParams();
  const [cap] = useChapterCap();
  const { data, isLoading, error } = useQuery({
    queryKey: ["object", novelId, objectId, cap],
    queryFn: () => api.object(novelId!, objectId!, cap),
    enabled: Boolean(novelId && objectId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data) return <p>Not found.</p>;
  return (
    <article>
      <h1>{data.identity.name}</h1>
      <section>
        <h2>Identity</h2>
        <FieldList
          fields={[
            { label: "Aliases", value: renderArray(data.identity.aliases) },
            { label: "Significance", value: data.identity.significance },
            { label: "First seen", value: data.identity.first_appearance_chapter ?? null },
            { label: "Description", value: data.identity.description },
          ]}
        />
      </section>
      <section>
        <h2>Ownership / Relationships ({data.relationships.length})</h2>
        {data.relationships.length === 0 ? (
          <p className="muted">No recorded relationships.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Character</th>
                <th>Type</th>
                <th>From ch.</th>
                <th>To ch.</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {data.relationships.map((r, i) => (
                <tr key={i}>
                  <td>{r.character_name}</td>
                  <td>{r.rel_type ?? "—"}</td>
                  <td>{r.from_chapter ?? "—"}</td>
                  <td>{r.to_chapter ?? "ongoing"}</td>
                  <td>{r.notes ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section>
        <h2>Characters involved ({data.characters.length})</h2>
        {data.characters.length === 0 ? (
          <p className="muted">None within cap.</p>
        ) : (
          <ul>
            {data.characters.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h2>Events ({data.events.length})</h2>
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
              </tr>
            </thead>
            <tbody>
              {data.events.map((e) => (
                <tr key={e.id}>
                  <td>{e.chapter_number}</td>
                  <td>{e.description}</td>
                  <td>{e.event_type ?? "—"}</td>
                  <td>{e.impact_level ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </article>
  );
}
