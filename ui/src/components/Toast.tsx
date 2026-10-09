export interface Notice {
  id: number;
  text: string;
  kind: "info" | "success" | "error";
}

export function Toast({ notice }: { notice: Notice | null }) {
  if (!notice) return null;
  return (
    <div key={notice.id} className={`toast ${notice.kind}`} role="status" aria-live="polite">
      {notice.text}
    </div>
  );
}
