import { describe, expect, it } from "vitest";
import {
  bytesToBase64,
  CSV_RULE,
  checkFile,
  formatBytes,
  PDF_MAX_BYTES,
  PDF_RULE,
  readFileAsBase64,
} from "./files";

describe("settings files", () => {
  it("accepts a PDF up to 5 MB and refuses others", () => {
    expect(checkFile({ name: "cv.PDF", size: 1000, type: "application/pdf" }, PDF_RULE)).toBeNull();
    expect(checkFile({ name: "cv.docx", size: 1000, type: "" }, PDF_RULE)).toBe(
      "Choose a PDF file (.pdf).",
    );
    expect(checkFile({ name: "cv.pdf", size: 10, type: "image/png" }, PDF_RULE)).toBe(
      "Choose a PDF file (.pdf).",
    );
    expect(checkFile({ name: "cv.pdf", size: 0, type: "application/pdf" }, PDF_RULE)).toBe(
      "That file is empty.",
    );
    expect(
      checkFile({ name: "cv.pdf", size: PDF_MAX_BYTES + 1, type: "application/pdf" }, PDF_RULE),
    ).toBe("That file is 5.0 MB; the limit is 5 MB.");
  });

  it("accepts a CSV up to 2 MB", () => {
    expect(checkFile({ name: "Connections.csv", size: 10, type: "text/csv" }, CSV_RULE)).toBeNull();
    expect(
      checkFile({ name: "Connections.csv", size: 3 * 1024 * 1024, type: "text/csv" }, CSV_RULE),
    ).toMatch(/That file is 3.0 MB; the limit is 2 MB/);
  });

  it("formats sizes", () => {
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(2048)).toBe("2 KB");
  });

  it("encodes bytes as base64 without a data: prefix", async () => {
    expect(bytesToBase64(new TextEncoder().encode("%PDF-1.7"))).toBe("JVBERi0xLjc=");
    const progress: number[] = [];
    const b64 = await readFileAsBase64(new Blob(["a,b\n1,2\n"]), (f) => progress.push(f));
    expect(atob(b64)).toBe("a,b\n1,2\n");
    expect(progress.at(-1)).toBe(1);
  });
});
