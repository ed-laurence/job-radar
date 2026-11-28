import { describe, expect, it } from "vitest";
import { ApiError, type Problem } from "@/lib/api";
import { buildAnnouncements } from "./announcements";
import {
  applyMove,
  columnDropId,
  groupByColumn,
  moveErrorMessage,
  rankAtEnd,
  resolveDrop,
  type Task,
} from "./model";

function task(id: string, column: Task["column"], rank: string, title = `Task ${id}`): Task {
  return {
    id,
    title,
    column,
    rank,
    version: 1,
    created_at: "2026-11-25T00:00:00Z",
    updated_at: "2026-11-25T00:00:00Z",
  };
}

const TASKS = [
  task("a", "backlog", "b"),
  task("b", "backlog", "a"),
  task("c", "backlog", "c"),
  task("d", "triaged", "m"),
];

function problem(status: number, type: string, extra: Partial<Problem> = {}): ApiError {
  return new ApiError({ type, title: "t", status, correlation_id: "corr-1", ...extra });
}

describe("groupByColumn", () => {
  it("groups every column and sorts by rank in byte order", () => {
    const grouped = groupByColumn([...TASKS, task("e", "backlog", "B")]);
    expect(Object.keys(grouped)).toHaveLength(11);
    // "B" < "a" in byte order (COLLATE "C"), unlike localeCompare.
    expect(grouped.backlog.map((t) => t.id)).toEqual(["e", "b", "a", "c"]);
    expect(grouped.done).toEqual([]);
  });
});

describe("rankAtEnd", () => {
  it("ranks after the last card of the target column", () => {
    expect(rankAtEnd(TASKS, "backlog", "z") > "c").toBe(true);
    expect(rankAtEnd(TASKS, "done", "z")).not.toBe("z");
  });

  it("falls back when the board isn't loaded", () => {
    expect(rankAtEnd(undefined, "done", "k")).toBe("k");
  });
});

describe("resolveDrop", () => {
  it("drops onto an empty column at the end", () => {
    const drop = resolveDrop(TASKS, "a", columnDropId("ready"));
    expect(drop?.toColumn).toBe("ready");
    expect(drop?.rank).toMatch(/^[0-9A-Za-z]+$/);
  });

  it("drops onto a column after its last card", () => {
    const drop = resolveDrop(TASKS, "a", columnDropId("triaged"));
    expect(drop?.toColumn).toBe("triaged");
    expect((drop?.rank ?? "") > "m").toBe(true);
  });

  it("drops before a card in another column", () => {
    const drop = resolveDrop(TASKS, "a", "d");
    expect(drop?.toColumn).toBe("triaged");
    expect((drop?.rank ?? "z") < "m").toBe(true);
  });

  it("reorders within a column, after the card when moving down", () => {
    // Order is b(a), a(b), c(c); moving b over c places it after c.
    const drop = resolveDrop(TASKS, "b", "c");
    expect(drop?.toColumn).toBe("backlog");
    expect((drop?.rank ?? "") > "c").toBe(true);
    // Moving c over b places it before b.
    const up = resolveDrop(TASKS, "c", "b");
    expect((up?.rank ?? "z") < "a").toBe(true);
  });

  it("is a no-op when dropped on itself or nowhere", () => {
    expect(resolveDrop(TASKS, "a", "a")).toBeNull();
    expect(resolveDrop(TASKS, "a", null)).toBeNull();
    expect(resolveDrop(TASKS, "a", columnDropId("backlog"))).toBeNull();
  });
});

describe("applyMove", () => {
  it("moves the card optimistically without touching others", () => {
    const next = applyMove(TASKS, "a", "triaged", "z");
    expect(next.find((t) => t.id === "a")).toMatchObject({ column: "triaged", rank: "z" });
    expect(next.find((t) => t.id === "b")).toBe(TASKS[1]);
  });
});

describe("moveErrorMessage", () => {
  const card = task("a", "backlog", "a", "Add empty state");

  it("explains a conflict", () => {
    expect(moveErrorMessage(problem(409, "/problems/version-conflict"), card, "triaged")).toMatch(
      /changed elsewhere/i,
    );
  });

  it("explains an illegal transition with the detail and the allowed columns", () => {
    const msg = moveErrorMessage(
      problem(422, "/problems/illegal-transition", {
        detail: "Only triage moves cards out of Backlog.",
        allowed_columns: ["blocked"],
      }),
      card,
      "done",
    );
    expect(msg).toContain("Can't move “Add empty state” from Backlog to Done");
    expect(msg).toContain("Only triage moves cards out of Backlog.");
    expect(msg).toContain("Allowed: Blocked");
  });

  it("explains an unapproved plan", () => {
    expect(moveErrorMessage(problem(422, "/problems/plan-not-approved"), card, "ready")).toMatch(
      /approve the plan/i,
    );
  });

  it("explains a maintainer-only move", () => {
    expect(moveErrorMessage(problem(403, "/problems/forbidden"), card, "ready")).toMatch(
      /only a maintainer can move a card to Ready/i,
    );
  });

  it("falls back to a generic message with the reference", () => {
    const msg = moveErrorMessage(problem(500, "/problems/internal"), card, "ready");
    expect(msg).toMatch(/couldn't move/i);
    expect(msg).toContain("corr-1");
  });
});

describe("announcements", () => {
  const ann = buildAnnouncements(() => TASKS);
  const active = { id: "a", data: { current: undefined }, rect: { current: {} } } as never;
  const over = (id: string) => ({ id, data: { current: undefined } }) as never;

  it("names the card and the columns", () => {
    expect(ann.onDragStart({ active })).toBe("Picked up “Task a” in Backlog.");
    expect(ann.onDragOver({ active, over: over(columnDropId("ready")) })).toBe(
      "“Task a” is over Ready.",
    );
    expect(ann.onDragOver({ active, over: over("d") })).toBe(
      "“Task a” is over “Task d” in Triaged.",
    );
    expect(ann.onDragEnd({ active, over: over(columnDropId("ready")) })).toBe(
      "Dropped “Task a” in Ready. Saving…",
    );
    expect(ann.onDragCancel({ active, over: null })).toBe(
      "Move cancelled. “Task a” is back in Backlog.",
    );
  });
});
