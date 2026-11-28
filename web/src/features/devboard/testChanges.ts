const MODIFY_TESTS = "modify-tests:";

function normalize(path: string): string {
  return path.trim().replace(/^\.\//, "").replace(/\/+$/, "");
}

/** Paths granted by the card's `modify-tests:` labels (each value is one path or a comma list). */
function grantedPaths(labels: readonly string[]): string[] {
  return labels
    .filter((l) => l.startsWith(MODIFY_TESTS))
    .flatMap((l) => l.slice(MODIFY_TESTS.length).split(","))
    .map(normalize)
    .filter((p) => p.length > 0);
}

/**
 * The plan's `tests_to_change` not yet covered by a `modify-tests:` label. A granted path covers
 * itself and, as a directory prefix, every file under it. Approving with any left over needs
 * `allow_test_changes` (the server returns 422 otherwise).
 */
export function uncoveredTestChanges(
  testsToChange: readonly string[],
  labels: readonly string[],
): string[] {
  const granted = grantedPaths(labels);
  return testsToChange.filter((path) => {
    const p = normalize(path);
    return !granted.some((g) => p === g || p.startsWith(`${g}/`));
  });
}
