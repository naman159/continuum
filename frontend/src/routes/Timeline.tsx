import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Timeline() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const { data, isLoading } = useQuery({
    queryKey: ["timeline", novelId, cap],
    queryFn: () => api.timeline(novelId!, cap),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (!data) return null;
  const grouped = new Map<number, typeof data>();
  for (const e of data) {
    if (!grouped.has(e.chapter_number)) grouped.set(e.chapter_number, []);
    grouped.get(e.chapter_number)!.push(e);
  }
  return (
    <div>
      <h1>Timeline</h1>
      {[...grouped.entries()].map(([chapter, events]) => (
        <section key={chapter}>
          <h2>Chapter {chapter}</h2>
          <table>
            <thead><tr><th>Description</th><th>Type</th><th>Impact</th><th>With</th></tr></thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td>{e.description}</td>
                  <td>{e.event_type ?? "—"}</td>
                  <td>{e.impact_level ?? "—"}</td>
                  <td>{e.involved_characters.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
