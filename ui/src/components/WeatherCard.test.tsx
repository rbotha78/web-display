import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api } from "../api";
import { WeatherCard } from "./WeatherCard";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("saves weather settings and clears the private key input", async () => {
  vi.spyOn(api, "weatherConfig").mockResolvedValue({
    city: "St Albans", country: "GB", configured: false,
  });
  const save = vi.spyOn(api, "saveWeatherConfig").mockResolvedValue({
    city: "St Albans", country: "GB", configured: true,
  });
  render(<WeatherCard notify={vi.fn()} fail={vi.fn()} />);
  await screen.findByText("OpenWeather not configured");
  fireEvent.change(screen.getByLabelText("OpenWeather API key"), { target: { value: "private-key" } });
  fireEvent.click(screen.getByText("Save weather settings"));
  await waitFor(() => expect(save).toHaveBeenCalledWith({
    city: "St Albans", country: "GB", api_key: "private-key",
  }));
  expect(screen.getByLabelText("OpenWeather API key")).toHaveProperty("value", "");
});
