import { describe, expect, it } from "vitest";
import { RANK_DIGITS, rankBetween } from "./rank";

const VALID = /^[0-9A-Za-z]{1,64}$/;
// Byte order, as the server sorts (COLLATE "C").
const byteLess = (a: string, b: string) => a < b;

function seeded(seed: number) {
  let s = seed;
  return () => {
    s = (s * 1103515245 + 12345) % 2 ** 31;
    return s / 2 ** 31;
  };
}

describe("rankBetween", () => {
  it("uses digits in ascending byte order", () => {
    const sorted = [...RANK_DIGITS].sort();
    expect(sorted.join("")).toBe(RANK_DIGITS);
  });

  it("handles open ends", () => {
    const first = rankBetween(null, null);
    expect(first).toMatch(VALID);
    const before = rankBetween(null, "a0");
    expect(before && byteLess(before, "a0")).toBe(true);
    const after = rankBetween("a0", null);
    expect(after && byteLess("a0", after)).toBe(true);
  });

  it("finds a key strictly between neighbours, including trailing-zero keys", () => {
    for (const [a, b] of [
      ["a0", "a1"],
      ["a", "a01"],
      ["a0", "b"],
      ["az", "b"],
      ["zz", "zzz"],
      ["0", "1"],
      ["a0z", "a1"],
    ] as const) {
      const k = rankBetween(a, b);
      expect(k, `${a}..${b}`).toMatch(VALID);
      expect(k && byteLess(a, k) && byteLess(k, b), `${a} < ${k} < ${b}`).toBe(true);
    }
  });

  it("returns null when no key exists", () => {
    expect(rankBetween(null, "0")).toBeNull();
    expect(rankBetween("a", "a0")).toBeNull();
    expect(rankBetween("b", "a")).toBeNull();
  });

  it("keeps order over many random inserts", () => {
    const random = seeded(7);
    const keys: string[] = [];
    for (let i = 0; i < 300; i++) {
      const at = Math.floor(random() * (keys.length + 1));
      const k = rankBetween(keys[at - 1] ?? null, keys[at] ?? null);
      expect(k).not.toBeNull();
      keys.splice(at, 0, k as string);
    }
    for (let i = 1; i < keys.length; i++) {
      expect(byteLess(keys[i - 1] as string, keys[i] as string)).toBe(true);
    }
    expect(keys.every((k) => VALID.test(k))).toBe(true);
  });
});
