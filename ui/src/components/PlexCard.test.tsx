import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api } from "../api";
import { PlexCard } from "./PlexCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("saves a write-only token and never renders the saved token", async () => {
  vi.spyOn(api, "plexConfig").mockResolvedValue({
    server_url: "http://plex.local:32400", configured: true,
  });
  const save = vi.spyOn(api, "savePlexConfig").mockResolvedValue({
    server_url: "http://plex.local:32400", configured: true, server_name: "Media server",
  });
  render(<PlexCard notify={vi.fn()} fail={vi.fn()} />);
  await screen.findByText("Configured server: http://plex.local:32400");
  fireEvent.change(screen.getByLabelText("Plex token"), { target: { value: "new-private-token" } });
  fireEvent.click(screen.getByText("Save and test connection"));
  await waitFor(() => expect(save).toHaveBeenCalledWith({
    server_url: "http://plex.local:32400", token: "new-private-token",
  }));
  expect(document.body.textContent).not.toContain("new-private-token");
  expect(screen.getByLabelText("Plex token")).toHaveProperty("value", "");
});
