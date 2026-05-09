import type { ReactNode } from "react";

type Field = { label: string; value: ReactNode };

export default function FieldList({ fields }: { fields: Field[] }) {
  return (
    <dl className="field-list">
      {fields.map(({ label, value }) => (
        <div key={label} style={{ display: "contents" }}>
          <dt>{label}</dt>
          <dd>{value ?? <span className="muted">—</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

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
