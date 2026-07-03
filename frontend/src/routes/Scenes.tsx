import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useChapterCap } from "../hooks/useChapterCap";

export default function Scenes() {
  const { novelId } = useParams();
  const [cap] = useChapterCap();
  const [chapter, setChapter] = useState<number | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["scenes", novelId, cap, chapter],
    queryFn: () => api.scenes(novelId!, cap, chapter),
    enabled: Boolean(novelId),
  });

  if (isLoading) return <p>Loading…</p>;

  // Group scenes by chapter.
  const byChapter = new Map<number, typeof data>();
  (data ?? []).forEach((s) => {
    const arr = byChapter.get(s.chapter_number) ?? [];
    arr.push(s);
    byChapter.set(s.chapter_number, arr);
  });
  const chapterKeys = Array.from(byChapter.keys()).sort((a, b) => a - b);

  return (
    <div>
      <h1>Scenes</h1>
      <p className="muted">
        Scene-level segmentation produced by the extractor's
        <code> scene_segmentation </code> pass.
      </p>
      <label>
        Filter to chapter:{" "}
        <input
          type="number"
          min={1}
          value={chapter ?? ""}
          placeholder="all"
          onChange={(e) => setChapter(e.target.value ? Number(e.target.value) : null)}
          style={{ width: 80 }}
        />
        {chapter != null && (
          <button onClick={() => setChapter(null)} style={{ marginLeft: 8 }}>
            Clear
          </button>
        )}
      </label>

      {chapterKeys.length === 0 && <p className="muted">No scenes extracted yet.</p>}

      {chapterKeys.map((n) => (
        <section key={n} style={{ marginTop: 24 }}>
          <h2>Chapter {n}</h2>
          {byChapter.get(n)!.map((s) => (
            <details key={s.id} open>
              <summary>
                <strong>
                  Scene {s.scene_index}
                  {s.pov_character_name ? ` — POV: ${s.pov_character_name}` : ""}
                </strong>
                {s.location_name ? ` · ${s.location_name}` : ""}
                {s.time_anchor ? ` · ${s.time_anchor}` : ""}
              </summary>
              {s.summary && <p>{s.summary}</p>}
              {s.present_character_names.length > 0 && (
                <p className="muted">
                  Present: {s.present_character_names.join(", ")}
                </p>
              )}
            </details>
          ))}
        </section>
      ))}
    </div>
  );
}
