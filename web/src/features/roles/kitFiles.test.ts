import { describe, expect, it } from "vitest";
import { filenameFrom } from "./kitFiles";

describe("filenameFrom", () => {
  it("reads quoted, bare and RFC 5987 filenames", () => {
    expect(filenameFrom('attachment; filename="Alex_Example_CV.pdf"', "x.pdf")).toBe(
      "Alex_Example_CV.pdf",
    );
    expect(filenameFrom("inline; filename=Letter.pdf", "x.pdf")).toBe("Letter.pdf");
    expect(filenameFrom("attachment; filename*=UTF-8''Caf%C3%A9_CV.pdf", "x.pdf")).toBe(
      "Café_CV.pdf",
    );
  });

  it("falls back when missing and strips path parts", () => {
    expect(filenameFrom(null, "Cover_Letter.pdf")).toBe("Cover_Letter.pdf");
    expect(filenameFrom("attachment", "CV.pdf")).toBe("CV.pdf");
    expect(filenameFrom('attachment; filename="../../etc/passwd"', "CV.pdf")).toBe("passwd");
  });
});
