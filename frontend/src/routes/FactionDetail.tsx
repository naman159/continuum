import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api";
import FieldList, { renderArray } from "../components/FieldList";
import { useChapterCap } from "../hooks/useChapterCap";

export default function FactionDetail() {
  const { novelId, factionId } = useParams();
  const [cap] = useChapterCap();
  const { data, isLoading, error } = useQuery({
    queryKey: ["faction", novelId, factionId, cap],
    queryFn: () => api.faction(novelId!, factionId!, cap),
    enabled: Boolean(novelId && factionId),
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
            { label: "Description", value: data.identity.description },
          ]}
        />
      </section>
    </article>
  );
}
