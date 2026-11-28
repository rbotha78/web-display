import { useState } from "react";

interface Props {
  title: string;
  children: React.ReactNode;
}

export function CollapsibleCard({ title, children }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <details open={open} onToggle={(event) => {
      if (event.currentTarget.open) setOpen(true);
    }} className="collapsible-card">
      <summary
        onClick={(event) => {
          event.preventDefault();
          setOpen(!open);
        }}
      >
        <h2>{title}</h2>
        <span className="chevron">
          {open ? "▾" : "▸"}
        </span>
      </summary>
      {children}
    </details>
  );
}
