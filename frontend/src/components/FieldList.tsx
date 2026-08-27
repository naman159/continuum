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

