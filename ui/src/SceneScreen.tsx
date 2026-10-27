import { useEffect, useState } from "react";
import type { Scene } from "./api";

interface DisplayScene {
  kind: "dashboard" | "recovery";
  revision: string;
  layout: Scene["layout"];
  text: string;
}

export function SceneScreen() {
  const [scene, setScene] = useState<DisplayScene | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch("/api/scene", { signal: controller.signal });
        if (!response.ok) throw new Error("Could not load the dashboard");
        const value: DisplayScene = await response.json();
        if (!controller.signal.aborted) {
          setScene((previous) => previous?.revision === value.revision ? previous : value);
          setError("");
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setError(error instanceof Error ? error.message : "Could not load the dashboard");
        }
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(poll, 500);
      }
    };
    void poll();
    const clock = window.setInterval(() => setNow(new Date()), 1000);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      window.clearInterval(clock);
    };
  }, []);

  useEffect(() => {
    if (!scene) return;
    const controller = new AbortController();
    fetch(`/api/scene/ready?revision=${encodeURIComponent(scene.revision)}`, { signal: controller.signal })
      .then((response) => { if (!response.ok) throw new Error("Could not start the scene timer"); })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setError(error instanceof Error ? error.message : "Could not start the scene timer");
        }
      });
    return () => controller.abort();
  }, [scene]);

  const size = (scene?.text.length ?? 0) > 500 ? "small" : (scene?.text.length ?? 0) > 200 ? "medium" : "large";
  return (
    <main className={`scene-screen ${scene?.layout ?? ""}`}>
      {error ? <p role="alert">{error}</p> : !scene ? <p>Loading scene...</p> :
        scene.kind === "recovery" ? (
          <div>
            <h1>No carousel scenes are currently available</h1>
            <p>Failed scenes will be retried automatically. Check the playlist in device administration.</p>
          </div>
        ) : (
          <>
            {scene.layout !== "notice" && (
              <div className="scene-clock">
                <time dateTime={now.toISOString()}>{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
                <p>{now.toLocaleDateString([], { weekday: "long", year: "numeric", month: "long", day: "numeric" })}</p>
              </div>
            )}
            {scene.layout !== "clock" && <p className={`scene-notice ${size}`}>{scene.text}</p>}
          </>
        )}
    </main>
  );
}
