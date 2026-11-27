import { describe, expect, it } from "vitest";
import {
  ApiError,
  currentVersionOf,
  isApiError,
  isProblem,
  isVersionConflict,
  toApiError,
} from "./errors";

const conflict = {
  type: "/problems/version-conflict",
  title: "Version conflict",
  status: 409,
  correlation_id: "c0ffee00-0000-4000-8000-000000000001",
  current_version: 4,
};

describe("isProblem", () => {
  it("accepts an RFC 9457 body with a correlation id", () => {
    expect(isProblem(conflict)).toBe(true);
  });

  it.each([null, "oops", 42, { title: "x" }, { ...conflict, status: "409" }])(
    "rejects %j",
    (value) => {
      expect(isProblem(value)).toBe(false);
    },
  );
});

describe("toApiError", () => {
  it("keeps the problem body and correlation id", () => {
    const err = toApiError(409, conflict, null);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.problem.type).toBe("/problems/version-conflict");
    expect(err.correlationId).toBe(conflict.correlation_id);
    expect(err.message).toBe("Version conflict");
  });

  it("wraps a non-problem body without leaking its text", () => {
    const err = toApiError(502, "<html>upstream stack trace</html>", "corr-1");
    expect(err.problem.type).toBe("about:blank");
    expect(err.problem.status).toBe(502);
    expect(err.correlationId).toBe("corr-1");
    expect(JSON.stringify(err.problem)).not.toContain("stack");
  });
});

describe("version conflict helpers", () => {
  it("detects a 409 version-conflict and reads current_version", () => {
    const err = toApiError(409, conflict, null);
    expect(isVersionConflict(err)).toBe(true);
    expect(currentVersionOf(err)).toBe(4);
  });

  it("does not treat other errors as conflicts", () => {
    const err = toApiError(404, { ...conflict, type: "/problems/not-found", status: 404 }, null);
    expect(isVersionConflict(err)).toBe(false);
    expect(currentVersionOf(err)).toBeUndefined();
    expect(isVersionConflict(new Error("x"))).toBe(false);
    expect(isApiError("x")).toBe(false);
  });
});
