import { describe, expect, it } from "vitest";
import { describeError, describeProblem, isNotFound } from "./describe";
import { ApiError } from "./errors";

const problem = {
  type: "/problems/validation-failed",
  title: "Validation failed",
  status: 422,
  detail: "Check the highlighted fields.",
  correlation_id: "corr-test-0001",
  errors: [{ pointer: "/name", message: "Name is required" }],
};

describe("describeError", () => {
  it("shows the problem title, detail, field messages and reference", () => {
    expect(describeError(new ApiError(problem))).toEqual({
      title: "Validation failed",
      description: "Check the highlighted fields.",
      correlationId: "corr-test-0001",
      fieldErrors: ["Name is required"],
    });
  });

  it("falls back to a connection hint for non-API failures", () => {
    expect(describeError(new TypeError("Failed to fetch"), "Save failed")).toMatchObject({
      title: "Save failed",
      description: "Check your connection and try again.",
    });
  });

  it("describes a stream's terminal problem and recognises 404s", () => {
    expect(describeProblem(undefined, "Research failed").title).toBe("Research failed");
    expect(isNotFound(new ApiError({ ...problem, status: 404 }))).toBe(true);
  });
});
