import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { SceneScreen } from "./SceneScreen";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {window.location.pathname === "/scene" ? <SceneScreen /> : <App />}
  </StrictMode>,
);
