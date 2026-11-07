import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api, setCsrf } from "../api";
import { RailDataCard } from "./RailDataCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setCsrf("");
});

test("saves the API key without exposing it in the returned configuration", async () => {
  vi.spyOn(api, "railDataConfig").mockResolvedValue({
    board_configured: false, details_configured: false, refresh_seconds: 60,
  });
  const save = vi.spyOn(api, "saveRailDataConfig").mockResolvedValue({
    board_configured: true, details_configured: true, refresh_seconds: 60,
  });
  const notify = vi.fn();
  render(<RailDataCard notify={notify} fail={vi.fn()} />);
  await screen.findByText(/Departure Board not configured/);
  fireEvent.change(screen.getByLabelText("Departure Board API key"), {
    target: { value: "board-key" },
  });
  fireEvent.change(screen.getByLabelText("Service Details API key"), {
    target: { value: "details-key" },
  });
  fireEvent.click(screen.getByText("Save National Rail settings"));
  await waitFor(() => expect(save).toHaveBeenCalledWith({
    api_key: "board-key", service_details_api_key: "details-key", refresh_seconds: 60,
  }));
  expect(screen.getByLabelText("Departure Board API key")).toHaveProperty("value", "");
  expect(screen.getByLabelText("Service Details API key")).toHaveProperty("value", "");
  expect(notify).toHaveBeenCalledWith("National Rail settings saved on this device.", "success");
});

test("saves a refresh interval while keeping existing credentials", async () => {
  vi.spyOn(api, "railDataConfig").mockResolvedValue({
    board_configured: true, details_configured: true, refresh_seconds: 120,
  });
  const save = vi.spyOn(api, "saveRailDataConfig").mockResolvedValue({
    board_configured: true, details_configured: true, refresh_seconds: 30,
  });
  render(<RailDataCard notify={vi.fn()} fail={vi.fn()} />);
  await waitFor(() => expect(screen.getByLabelText("Refresh interval (seconds)"))
    .toHaveProperty("value", "120"));
  fireEvent.change(screen.getByLabelText("Refresh interval (seconds)"), {
    target: { value: "30" },
  });
  fireEvent.click(screen.getByText("Save National Rail settings"));
  await waitFor(() => expect(save).toHaveBeenCalledWith({
    api_key: "", service_details_api_key: "", refresh_seconds: 30,
  }));
});
