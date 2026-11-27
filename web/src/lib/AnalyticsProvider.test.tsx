import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AnalyticsProvider, useAnalytics } from "./AnalyticsProvider";
import type { WebEvent } from "./analytics";

vi.mock("next/navigation", () => ({
  usePathname: () => "/roles/0b6f3a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b",
}));
vi.mock("web-vitals", () => ({
  onLCP: (cb: (m: { name: string; value: number; rating: string }) => void) =>
    cb({ name: "LCP", value: 1200, rating: "good" }),
  onINP: () => {},
  onCLS: () => {},
  onFCP: () => {},
  onTTFB: () => {},
}));

function MoveButton() {
  const { track } = useAnalytics();
  return (
    <button type="button" onClick={() => track("role_moved")}>
      Move
    </button>
  );
}

describe("AnalyticsProvider", () => {
  it("sends page views, vitals and product events with route templates on page hide", async () => {
    const sent: WebEvent[] = [];
    const send = vi.fn(async (events: WebEvent[]) => {
      sent.push(...events);
    });
    render(
      <AnalyticsProvider send={send}>
        <MoveButton />
      </AnalyticsProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Move" }));

    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(send).toHaveBeenCalledWith(expect.any(Array), { keepalive: true });
    expect(sent.map((e) => e.type).sort()).toEqual(["page_view", "product", "web_vital"]);
    expect(new Set(sent.map((e) => e.route))).toEqual(new Set(["/roles/[roleId]"]));
    expect(JSON.stringify(sent)).not.toContain("0b6f3a8e");
  });
});
