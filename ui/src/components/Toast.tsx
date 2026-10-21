import { useEffect, useState } from "react";

export interface Notice {
  id: number;
  text: string;
  kind: "info" | "success" | "error";
}

export function Toast({ notice }: { notice: Notice | null }) {
  const [dismissed, setDismissed] = useState<Notice | null>(null);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setDismissed(notice), notice.kind === "error" ? 10000 : 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  if (!notice || notice === dismissed) return null;
  return (
    <div key={notice.id} className={`toast ${notice.kind}`} role="status" aria-live="polite">
      {notice.text}
      <button type="button" className="ghost" aria-label="Dismiss notification" onClick={() => setDismissed(notice)}>
        Dismiss
      </button>
    </div>
  );
}
