"use client";

import { unwrap, useApiClient } from "@/lib/api";

export type KitFile = "cover-letter" | "cv";

/** The file name from a Content-Disposition header (RFC 6266 / 5987), without any path parts. */
export function filenameFrom(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const star = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(disposition);
  const plain = /filename\s*=\s*(?:"([^"]*)"|([^;\s]+))/.exec(disposition);
  let name: string | undefined;
  if (star?.[1]) {
    try {
      name = decodeURIComponent(star[1].trim());
    } catch {
      name = undefined;
    }
  }
  name ??= plain?.[1] ?? plain?.[2];
  const base = name?.split(/[/]/).pop()?.trim();
  return base || fallback;
}

function save(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
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

/**
 * Downloads a kit PDF. A plain link can't carry the bearer token, so the file is fetched through
 * the BFF first and saved from a blob, under the name the server gives it.
 */
export function useKitDownload() {
  const client = useApiClient();
  return async (roleId: string, file: KitFile): Promise<void> => {
    const params = { path: { roleId }, query: { download: true } };
    const result =
      file === "cv"
        ? client.GET("/roles/{roleId}/kit/cv.pdf", { params, parseAs: "blob" })
        : client.GET("/roles/{roleId}/kit/cover-letter.pdf", { params, parseAs: "blob" });
    const { response } = await result;
    const blob = await unwrap(result);
    const fallback = file === "cv" ? "CV.pdf" : "Cover_Letter.pdf";
    save(blob as Blob, filenameFrom(response.headers.get("content-disposition"), fallback));
  };
}
