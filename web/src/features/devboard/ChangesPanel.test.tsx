import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { TaskDiff } from "./api";
import { ChangesPanel } from "./ChangesPanel";

const DIFF: TaskDiff = {
  branch: "agent/0000b1aa-0000f2bb",
  base_sha: "0123456789abcdef0123456789abcdef01234567",
  files_changed: 2,
  insertions: 3,
  deletions: 1,
  truncated: false,
  diff: [
    "diff --git a/web/src/a.ts b/web/src/a.ts",
    "--- a/web/src/a.ts",
    "+++ b/web/src/a.ts",
    "@@ -1,2 +1,2 @@",
    " keep",
    "-const b = 2;",
    "+const b = 3;",
    "diff --git a/web/src/b.ts b/web/src/c.ts",
    "rename from web/src/b.ts",
    "rename to web/src/c.ts",
    "--- a/web/src/b.ts",
    "+++ b/web/src/c.ts",
    "@@ -1 +1,3 @@",
    " x",
    "+y",
    "+z",
    "",
  ].join("\n"),
};

function renderPanel(overrides: Partial<Parameters<typeof ChangesPanel>[0]> = {}) {
  const onDownloadPatch = vi.fn();
  render(
    <ChangesPanel
      diff={DIFF}
      loading={false}
      downloading={false}
      onDownloadPatch={onDownloadPatch}
      {...overrides}
    />,
  );
  return { onDownloadPatch, panel: screen.getByRole("region", { name: "Changes" }) };
}

describe("ChangesPanel", () => {
  it("shows the branch, the short base SHA and the diff stat", () => {
    const { panel } = renderPanel();
    expect(within(panel).getByText("agent/0000b1aa-0000f2bb").tagName).toBe("CODE");
    const sha = within(panel).getByText("0123456");
    expect(sha.tagName).toBe("CODE");
    expect(sha).toHaveAttribute("title", DIFF.base_sha);
    expect(within(panel).getByText(/2 files changed/)).toBeInTheDocument();
    expect(within(panel).getByText("+3")).toBeInTheDocument();
    expect(within(panel).getByText("−1")).toBeInTheDocument();
  });

  it("renders one collapsible section per file with added and removed lines", () => {
    const { panel } = renderPanel();
    const groups = panel.querySelectorAll("details");
    expect(groups).toHaveLength(2);
    const first = groups[0] as HTMLElement;
    expect(within(first).getByText("web/src/a.ts")).toBeInTheDocument();
    const added = within(first).getByText("+const b = 3;");
    const removed = within(first).getByText("-const b = 2;");
    expect(added).toHaveAttribute("data-kind", "added");
    expect(removed).toHaveAttribute("data-kind", "removed");
    const second = groups[1] as HTMLElement;
    expect(within(second).getByText("web/src/c.ts")).toBeInTheDocument();
    expect(within(second).getByText(/renamed from/)).toHaveTextContent("web/src/b.ts");
  });

  it("says when the diff was cut short", () => {
    const { panel } = renderPanel({ diff: { ...DIFF, truncated: true } });
    expect(within(panel).getByText(/diff is too large to show in full/i)).toBeInTheDocument();
  });

  it("has no truncation notice for a complete diff", () => {
    const { panel } = renderPanel();
    expect(within(panel).queryByText(/too large/i)).toBeNull();
  });

  it("offers the patch download", async () => {
    const { onDownloadPatch, panel } = renderPanel();
    await userEvent.setup().click(within(panel).getByRole("button", { name: "Download patch" }));
    expect(onDownloadPatch).toHaveBeenCalledOnce();
  });

  it("disables the download while it is in progress and shows its error", () => {
    const { panel } = renderPanel({ downloading: true, downloadError: "Couldn't download." });
    expect(within(panel).getByRole("button", { name: /Download/ })).toBeDisabled();
    expect(within(panel).getByRole("alert")).toHaveTextContent("Couldn't download.");
  });

  it("shows loading and missing states", () => {
    const { rerender } = render(
      <ChangesPanel diff={undefined} loading downloading={false} onDownloadPatch={() => {}} />,
    );
    expect(screen.getByText(/Loading the changes/)).toBeInTheDocument();
    rerender(
      <ChangesPanel diff={null} loading={false} downloading={false} onDownloadPatch={() => {}} />,
    );
    expect(screen.getByText(/No changes published yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download patch" })).toBeNull();
  });
});
