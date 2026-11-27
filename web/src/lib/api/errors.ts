import type { Schemas } from "./generated/client";

export type Problem = Schemas["Problem"];

/** A non-2xx API response, typed as RFC 9457 problem details. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;
  readonly correlationId: string | undefined;

  constructor(problem: Problem, correlationId?: string) {
    super(problem.title);
    this.name = "ApiError";
    this.status = problem.status;
    this.problem = problem;
    this.correlationId = correlationId || problem.correlation_id || undefined;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function isProblem(value: unknown): value is Problem {
  return (
    isRecord(value) &&
    typeof value.type === "string" &&
    typeof value.title === "string" &&
    typeof value.status === "number" &&
    typeof value.correlation_id === "string"
  );
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/**
 * Builds an ApiError from a failed response. A body that isn't a problem (a proxy error page,
 * say) is replaced by a generic problem so its text never reaches the UI.
 */
export function toApiError(
  status: number,
  body: unknown,
  correlationHeader: string | null,
): ApiError {
  if (isProblem(body)) {
    return new ApiError(body, correlationHeader ?? undefined);
  }
  const problem: Problem = {
    type: "about:blank",
    title: "Request failed",
    status,
    correlation_id: correlationHeader ?? "",
  };
  return new ApiError(problem, correlationHeader ?? undefined);
}

export function isVersionConflict(value: unknown): value is ApiError {
  return (
    isApiError(value) && value.status === 409 && value.problem.type === "/problems/version-conflict"
  );
}

/** The server's current version from a version-conflict error, if there is one. */
export function currentVersionOf(value: unknown): number | undefined {
  return isVersionConflict(value) ? value.problem.current_version : undefined;
}
