import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";

const backend = vi.hoisted(() => ({ session: vi.fn(), submit: vi.fn(), get: vi.fn(), cancel: vi.fn() }));
vi.mock("@/lib/flight.functions", () => ({
  getFlightSession: backend.session, submitFlightSearch: backend.submit,
  getFlightJob: backend.get, cancelFlightJob: backend.cancel,
}));
vi.mock("@/integrations/supabase/client", () => ({ supabase: { auth: { signOut: vi.fn() } } }));
// Server hooks may update React state around requests. Reproduce those rerenders
// rather than replacing the real form/event handling with a mock.
vi.mock("@tanstack/react-start", () => ({
  useServerFn: (fn: (...args: unknown[]) => Promise<unknown>) => {
    const [, setVersion] = useState(0);
    return async (...args: unknown[]) => {
      setVersion((v) => v + 1);
      try { return await fn(...args); }
      finally { setVersion((v) => v + 1); }
    };
  },
}));
import { FlightApp } from "./FlightApp";

const query = { origin: "HEL", destination: "HAN", departure: "2026-12-09", return_date: "2027-01-09", trip_type: "roundtrip", flex_days: 3, min_stay_nights: 21 };
const job = { id: "00000000-0000-4000-8000-000000000001", status: "queued", query, fares: [], providers: {}, message: "Waiting for worker", elapsed_seconds: 0 };
const worker = { online: false, state: "offline", last_seen_at: null };

beforeEach(() => {
  vi.clearAllMocks();
  backend.session.mockResolvedValue({ owner: true, job, worker });
  backend.get.mockResolvedValue({ job: { ...job, status: "running" }, worker: { ...worker, online: true } });
  backend.cancel.mockResolvedValue({ job: { ...job, status: "cancelled" }, worker });
  HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { cleanup(); delete document.body.dataset.tripType; });

describe("flight form during server updates", () => {
  it("keeps calendar, airport edits and event handlers working across a job poll", async () => {
    render(<FlightApp />);
    await waitFor(() => expect(backend.session).toHaveBeenCalledOnce());
    await waitFor(() => expect(document.querySelector<HTMLButtonElement>("#stop-search")?.hidden).toBe(false));
    const airport = document.querySelector<HTMLInputElement>("#origin-input")!;
    fireEvent.click(document.querySelector("#swap-button")!);
    expect(airport.value).toBe("Hanoi (HAN)");
    fireEvent.click(document.querySelector("#departure-button")!);
    expect(document.querySelector<HTMLDivElement>("#date-picker")!.hidden).toBe(false);
    await waitFor(() => expect(backend.get).toHaveBeenCalled(), { timeout: 4000 });
    expect(document.querySelector("#origin-input")).toBe(airport);
    expect(airport.value).toBe("Hanoi (HAN)");
    expect(document.querySelector<HTMLDivElement>("#date-picker")!.hidden).toBe(false);
    fireEvent.click(document.querySelector('[data-picker-action="close"]')!);
    fireEvent.click(document.querySelector("#swap-button")!);
    expect(airport.value).toBe("Helsinki (HEL)");
    fireEvent.click(document.querySelector('input[value="oneway"]')!);
    expect(document.body.dataset.tripType).toBe("oneway");
    await act(async () => { fireEvent.click(document.querySelector("#stop-search")!); });
    expect(backend.cancel).toHaveBeenCalledWith({ data: { id: job.id } });
    expect(document.querySelector<HTMLButtonElement>("#search-button")!.disabled).toBe(false);
  });

  it("cleans up polling when leaving the form", async () => {
    const page = render(<FlightApp />);
    await waitFor(() => expect(document.querySelector<HTMLButtonElement>("#stop-search")?.hidden).toBe(false));
    page.unmount();
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2100)); });
    expect(backend.get).not.toHaveBeenCalled();
    expect(document.querySelector("#search-card")).toBeNull();
  });
});
