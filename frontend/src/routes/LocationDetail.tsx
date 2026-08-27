import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";
import FieldList from "../components/FieldList";
import { renderArray } from "../components/fieldRenderers";
import { useChapterCap } from "../hooks/useChapterCap";

export default function LocationDetail() {
  const { novelId, locationId } = useParams();
  const [cap] = useChapterCap();
  const { data, isLoading, error } = useQuery({
    queryKey: ["location", novelId, locationId, cap],
    queryFn: () => api.location(novelId!, locationId!, cap),
    enabled: Boolean(novelId && locationId),
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
            { label: "First seen", value: data.identity.first_appearance_chapter ?? null },
            { label: "Description", value: data.identity.description },
          ]}
        />
      </section>
      <section>
        <h2>Characters here ({data.characters.length})</h2>
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
