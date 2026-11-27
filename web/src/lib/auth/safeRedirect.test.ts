import { describe, expect, it } from "vitest";
import { safeRedirectPath } from "./safeRedirect";

describe("safeRedirectPath", () => {
  it.each([
    ["/roles", "/roles"],
    ["/roles/abc?tab=kit", "/roles/abc?tab=kit"],
    [null, "/dashboard"],
    ["", "/dashboard"],
    ["https://evil.example.test", "/dashboard"],
    ["//evil.example.test", "/dashboard"],
    ["/\\evil.example.test", "/dashboard"],
    ["javascript:alert(1)", "/dashboard"],
    ["/sign-in?next=/x", "/dashboard"],
  ])("%s -> %s", (input, expected) => {
    expect(safeRedirectPath(input)).toBe(expected);
  });
});
