import { useCallback, useEffect, useState } from "react";
import { api, setCsrf } from "./api";
import { AuthCard } from "./components/AuthCard";
import { Dashboard } from "./components/Dashboard";
import { Toast, type Notice } from "./components/Toast";

type View = "loading" | "pair" | "login" | "admin";

export function App() {
  const [view, setView] = useState<View>("loading");
  const [notice, setNotice] = useState<Notice | null>(null);

  const notify = useCallback((text: string, kind: Notice["kind"] = "info") => {
    setNotice({ text, kind, id: Date.now() });
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setCsrf((await api.session()).csrf);
        if (!cancelled) setView("admin");
      } catch {
        try {
          const { paired } = await api.health();
          if (!cancelled) setView(paired ? "login" : "pair");
        } catch (error) {
          if (!cancelled) {
            notify(error instanceof Error ? error.message : "Cannot reach the device", "error");
            setView("login");
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [notify]);

  const signedIn = (csrf: string, text?: string) => {
    setCsrf(csrf);
    setView("admin");
    if (text) notify(text, "success");
  };

  const signOut = async () => {
    try {
      await api.logout();
    } catch {
      // The session is dropped locally either way.
    }
    setCsrf("");
    setView("login");
  };

  return (
    <div className="shell">
      <header className="topbar">
        <h1>Web Display</h1>
        {view === "admin" && (
          <button className="ghost" onClick={signOut}>
            Sign out
          </button>
        )}
      </header>
      <main>
        {view === "loading" && <p className="muted">Loading…</p>}
        {(view === "pair" || view === "login") && (
          <AuthCard
            mode={view}
            onDone={(csrf) => signedIn(csrf, view === "pair" ? "Pairing complete." : undefined)}
            notify={notify}
          />
        )}
        {view === "admin" && <Dashboard notify={notify} onExpired={() => setView("login")} />}
      </main>
      <Toast notice={notice} />
    </div>
  );
}
