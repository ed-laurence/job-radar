import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Role } from "./labels";
import { RolesTable } from "./RolesTable";

const role: Role = {
  id: "00000000-0000-4000-8000-0000000000b1",
  source: "feed",
  title: "Data Engineer",
  stage: "scored",
  status: "new",
  posted_at: "2026-11-24T12:00:00Z",
  found_at: "2026-11-25T09:00:00Z",
  rank: "a0",
  version: 1,
  created_at: "2026-11-25T09:00:00Z",
  updated_at: "2026-11-25T09:00:00Z",
};

describe("RolesTable", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-27T12:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("shows an Age column right after Found", () => {
    render(<RolesTable roles={[role]} />);
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers.slice(-2)).toEqual(["Found", "Age"]);
    const cells = within(screen.getAllByRole("row")[1] as HTMLElement).getAllByRole("cell");
    expect(cells.at(-1)).toHaveTextContent("3 days");
  });
});
