import { isApiError, type Problem } from "./errors";

export interface ErrorView {
  title: string;
  description: string | undefined;
  correlationId: string | undefined;
  fieldErrors: string[];
}

/** What the UI may show of a failure: the problem title, its redacted detail and the reference. */
export function describeError(error: unknown, fallback = "Something went wrong"): ErrorView {
  if (!isApiError(error)) {
    return {
      title: fallback,
      description: "Check your connection and try again.",
      correlationId: undefined,
      fieldErrors: [],
    };
  }
  return {
    title: error.problem.title || fallback,
    description: error.problem.detail,
    correlationId: error.correlationId,
    fieldErrors: (error.problem.errors ?? []).map((e) => e.message),
  };
}

export function isNotFound(error: unknown): boolean {
  return isApiError(error) && error.status === 404;
}

/** A stream's terminal problem, shown the same way as a failed request. */
export function describeProblem(problem: Problem | undefined, fallback: string): ErrorView {
  return {
    title: problem?.title || fallback,
    description: problem?.detail,
    correlationId: problem?.correlation_id || undefined,
    fieldErrors: [],
  };
}
