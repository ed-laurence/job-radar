import { isApiError } from "@/lib/api";

/**
 * A user-facing message for a failed write (save, delete, approve, kill), built only from the
 * problem details: title, redacted detail, field messages and the correlation id (SEC-07).
 */
export function actionErrorMessage(error: unknown, action: string): string {
  if (!isApiError(error)) return `Couldn't ${action}. Check your connection and try again.`;
  const { status, problem, correlationId } = error;
  if (status === 409) {
    return `This task changed elsewhere, so it has been reloaded. Review it and ${action} again.`;
  }
  if (status === 403) return `Only a maintainer can ${action}.`;
  if (status === 413) return "That's too long to save. Shorten it and try again.";
  if (status === 429) return "Too many requests. Wait a moment and try again.";
  const parts = [`Couldn't ${action}.`];
  if (status === 422 || status === 400) {
    if (problem.detail) parts.push(problem.detail);
    for (const e of problem.errors ?? []) parts.push(e.message);
  }
  if (correlationId) parts.push(`Reference: ${correlationId}`);
  return parts.join(" ");
}
