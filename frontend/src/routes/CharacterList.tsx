import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function CharacterList() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const location = useLocation();
  const { data, isLoading, error } = useQuery({
    queryKey: ["characters", novelId, cap],
    queryFn: () => api.characters(novelId!, cap),
    enabled: Boolean(novelId),
  });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data || data.length === 0) return <p>No characters.</p>;
  return (
    <div>
      <h1>Characters</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Aliases</th>
            <th>First seen</th>
          </tr>
        </thead>
        <tbody>
          {data.map((c) => (
            <tr key={c.id}>
              <td>
                <Link to={`/novels/${novelId}/characters/${c.id}${location.search}`}>{c.name}</Link>
              </td>
              <td>{c.aliases.join(", ") || "—"}</td>
              <td>{c.first_appearance_chapter ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
