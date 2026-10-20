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
    server_url: "http://plex.local:32400", configured: true, default_bitrate_kbps: 4000,
  });
  const save = vi.spyOn(api, "savePlexConfig").mockResolvedValue({
    server_url: "http://plex.local:32400", configured: true, server_name: "Media server",
    default_bitrate_kbps: 6000,
  });
  render(<PlexCard notify={vi.fn()} fail={vi.fn()} />);
  await screen.findByText("Configured server: http://plex.local:32400");
  fireEvent.change(screen.getByLabelText("Plex token"), { target: { value: "new-private-token" } });
  fireEvent.change(screen.getByLabelText("Default Plex bitrate (kbps)"), { target: { value: "6000" } });
  fireEvent.click(screen.getByText("Save and test connection"));
  await waitFor(() => expect(save).toHaveBeenCalledWith({
    server_url: "http://plex.local:32400", token: "new-private-token",
    default_bitrate_kbps: 6000,
  }));
  expect(document.body.textContent).not.toContain("new-private-token");
  expect(screen.getByLabelText("Plex token")).toHaveProperty("value", "");
});
