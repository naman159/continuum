import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";

export default function Novels() {
  const { data, isLoading, error } = useQuery({ queryKey: ["novels"], queryFn: api.novels });
  if (isLoading) return <p>Loading…</p>;
  if (error) return <p>Error: {(error as Error).message}</p>;
  if (!data || data.length === 0) return <p>No novels.</p>;
  return (
    <div>
      <h1>Novels</h1>
      <ul>
        {data.map((n) => (
          <li key={n.id}>
            <Link to={`/novels/${n.id}/characters`}>{n.title}</Link>
            {" — "}
            {n.author ?? "Unknown"} · {n.max_chapter} chapter{n.max_chapter === 1 ? "" : "s"}
          </li>
        ))}
      </ul>
    </div>
  );
}
