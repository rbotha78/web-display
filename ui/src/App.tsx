import { useCallback, useEffect, useRef, useState } from "react";
import { api, setCsrf, type Session } from "./api";
import { AuthCard } from "./components/AuthCard";
import { Dashboard } from "./components/Dashboard";
import { Toast, type Notice } from "./components/Toast";

type View = "loading" | "pair" | "login" | "admin";

export function App() {
  const [view, setView] = useState<View>("loading");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [canManageKeys, setCanManageKeys] = useState(true);
  const initialSession = useRef<Promise<Session> | null>(null);
  const managerLogin = useRef(false);

  const notify = useCallback((text: string, kind: Notice["kind"] = "info") => {
    setNotice({ text, kind, id: Date.now() });
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!initialSession.current) {
      const token = new URLSearchParams(window.location.hash.slice(1)).get("admin-login");
      managerLogin.current = token !== null;
      if (token !== null) window.history.replaceState(null, "", window.location.pathname + window.location.search);
      initialSession.current = token !== null ? api.managerLogin(token) : api.session();
    }
    const request = initialSession.current;
    (async () => {
      try {
        const session = await request;
        if (!cancelled) {
          setCsrf(session.csrf);
          setCanManageKeys(session.can_manage_keys !== false);
          setView("admin");
        }
      } catch (loginError) {
        if (!cancelled && managerLogin.current) {
          notify(loginError instanceof Error ? loginError.message : "Automatic login failed", "error");
        }
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
    setCanManageKeys(true);
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
        {view === "admin" && <Dashboard notify={notify} onExpired={() => setView("login")} canManageKeys={canManageKeys} />}
      </main>
      <Toast notice={notice} />
    </div>
  );
}
