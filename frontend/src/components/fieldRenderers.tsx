import type { ReactNode } from "react";

/** Value renderers shared by the entity detail pages.
 *
 * Kept out of FieldList.tsx so that file exports only its component — a
 * module that mixes component and non-component exports breaks React Fast
 * Refresh, which is what `react-refresh/only-export-components` guards. */

export function renderArray(values: string[] | null | undefined): ReactNode {
  if (!values || values.length === 0) return <span className="muted">—</span>;
  return values.map((v) => (
    <span key={v} className="tag">
      {v}
    </span>
  ));
}

export function renderJson(obj: Record<string, unknown> | null | undefined): ReactNode {
  if (!obj || Object.keys(obj).length === 0) return <span className="muted">—</span>;
  return <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(obj, null, 2)}</pre>;
}
