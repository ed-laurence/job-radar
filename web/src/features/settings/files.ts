/** Client-side checks and encoding for the files Settings uploads (the server checks again). */

export const PDF_MAX_BYTES = 5 * 1024 * 1024;
export const CSV_MAX_BYTES = 2 * 1024 * 1024;

export interface FileRule {
  maxBytes: number;
  /** Lower-case extension including the dot. */
  extension: string;
  mimeTypes: readonly string[];
  kind: string;
}

export const PDF_RULE: FileRule = {
  maxBytes: PDF_MAX_BYTES,
  extension: ".pdf",
  mimeTypes: ["application/pdf"],
  kind: "PDF",
};

export const CSV_RULE: FileRule = {
  maxBytes: CSV_MAX_BYTES,
  extension: ".csv",
  mimeTypes: ["text/csv", "application/vnd.ms-excel", "text/plain", ""],
  kind: "CSV",
};

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Why the file can't be sent, or null when it passes. */
export function checkFile(
  file: Pick<File, "name" | "size" | "type">,
  rule: FileRule,
): string | null {
  const name = file.name.toLowerCase();
  const typeOk = rule.mimeTypes.includes(file.type) || file.type === "";
  if (!name.endsWith(rule.extension) || !typeOk) {
    return `Choose a ${rule.kind} file (${rule.extension}).`;
  }
  if (file.size === 0) return "That file is empty.";
  if (file.size > rule.maxBytes) {
    return `That file is ${formatBytes(file.size)}; the limit is ${formatBytes(rule.maxBytes).replace(".0", "")}.`;
  }
  return null;
}

/** Base64 of the bytes (no data: prefix), chunked so large files don't overflow the stack. */
export function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

/** Reads a file as base64, reporting read progress from 0 to 1. */
export function readFileAsBase64(
  file: Blob,
  onProgress?: (fraction: number) => void,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress?.(e.loaded / e.total);
    };
    reader.onerror = () => reject(new Error("The file couldn't be read."));
    reader.onload = () => {
      const result = reader.result;
      if (!(result instanceof ArrayBuffer)) {
        reject(new Error("The file couldn't be read."));
        return;
      }
      onProgress?.(1);
      resolve(bytesToBase64(new Uint8Array(result)));
    };
    reader.readAsArrayBuffer(file);
  });
}
