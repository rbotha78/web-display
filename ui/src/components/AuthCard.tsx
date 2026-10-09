import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { Notice } from "./Toast";

interface Props {
  mode: "pair" | "login";
  onDone: (csrf: string) => void;
  notify: (text: string, kind?: Notice["kind"]) => void;
}

export function AuthCard({ mode, onDone, notify }: Props) {
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const pairing = mode === "pair";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const session = pairing ? await api.pair(code, password) : await api.login(password);
      onDone(session.csrf);
    } catch (error) {
      notify(error instanceof Error ? error.message : "Request failed", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card narrow" onSubmit={submit}>
      <h2>{pairing ? "First-use pairing" : "Administrator login"}</h2>
      {pairing && (
        <>
          <p className="muted">
            Enter the six-digit code shown on the display and choose an administrator password of
            at least 12 characters.
          </p>
          <label>
            Code shown on display
            <input
              value={code}
              onChange={(event) => setCode(event.target.value)}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              required
            />
          </label>
        </>
      )}
      <label>
        {pairing ? "New administrator password" : "Administrator password"}
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete={pairing ? "new-password" : "current-password"}
          minLength={pairing ? 12 : undefined}
          required
        />
      </label>
      <button className="primary" disabled={busy}>
        {pairing ? "Pair device" : "Log in"}
      </button>
    </form>
  );
}
