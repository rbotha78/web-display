import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Toast } from "./Toast";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("expires notices and gives a replacement its full lifetime", () => {
  vi.useFakeTimers();
  const { rerender } = render(<Toast notice={{ id: 1, text: "Saved", kind: "success" }} />);
  act(() => vi.advanceTimersByTime(4000));
  rerender(<Toast notice={{ id: 2, text: "Updated", kind: "info" }} />);
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.getByText("Updated")).toBeTruthy();
  act(() => vi.advanceTimersByTime(4000));
  expect(screen.queryByRole("status")).toBeNull();
});

test("keeps errors for ten seconds and supports immediate dismissal", () => {
  vi.useFakeTimers();
  const { rerender } = render(<Toast notice={{ id: 1, text: "Failed", kind: "error" }} />);
  act(() => vi.advanceTimersByTime(9999));
  expect(screen.getByText("Failed")).toBeTruthy();
  act(() => vi.advanceTimersByTime(1));
  expect(screen.queryByRole("status")).toBeNull();
  rerender(<Toast notice={{ id: 2, text: "Try again", kind: "error" }} />);
  fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
  expect(screen.queryByRole("status")).toBeNull();
  rerender(<Toast notice={{ id: 3, text: "Saved", kind: "success" }} />);
  expect(screen.getByText("Saved")).toBeTruthy();
});
