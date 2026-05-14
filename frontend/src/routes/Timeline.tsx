import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";

export default function Timeline() {
  const { novelId } = useParams();
  const { data, isLoading } = useQuery({
    queryKey: ["timeline", novelId],
    queryFn: () => api.timeline(novelId!),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (!data) return null;
  return (
    <div>
      <h1>Timeline</h1>
      {data.length === 0 && <p>No timeline entries yet.</p>}
      <ol>
        {data.map((e) => (
          <li key={e.id}>
            <strong>{e.description}</strong>
            {e.story_date && <span> &mdash; {e.story_date}</span>}
            {e.involved_characters.length > 0 && (
              <span> ({e.involved_characters.join(", ")})</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
