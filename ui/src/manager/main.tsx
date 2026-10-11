import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ManagerApp } from "./ManagerApp";
import "../styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ManagerApp />
  </StrictMode>,
);
