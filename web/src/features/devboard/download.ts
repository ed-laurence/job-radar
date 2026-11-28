/** Saves text as a file through a temporary blob URL (the API needs a bearer token, so no plain link). */
export function saveTextFile(text: string, filename: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.append(a);
  a.click();
  a.remove();
  // Give the browser a moment to start the download before freeing the blob.
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/** `agent/abc-def` → `agent-abc-def.patch`: path separators aren't allowed in file names. */
export function patchFilename(branch: string): string {
  const safe = branch.replace(/[/:*?"<>|\s]+/g, "-").replace(/^-+|-+$/g, "");
  return `${safe || "changes"}.patch`;
}
