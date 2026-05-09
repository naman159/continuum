import { useSearchParams } from "react-router-dom";

export function useChapterCap(): [number | null, (next: number | null) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("cap");
  const cap = raw == null || raw === "" ? null : Number(raw);
  const setCap = (next: number | null) => {
    const newParams = new URLSearchParams(params);
    if (next == null) {
      newParams.delete("cap");
    } else {
      newParams.set("cap", String(next));
    }
    setParams(newParams);
  };
  return [cap, setCap];
}
