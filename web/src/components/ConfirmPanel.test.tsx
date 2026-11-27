import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmPanel } from "./ConfirmPanel";

function setup(busy = false) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConfirmPanel
      title="Delete Acme Pty Ltd?"
      description="It is removed from your list."
      confirmLabel="Yes, delete"
      busy={busy}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />,
  );
  return { onConfirm, onCancel };
}

describe("ConfirmPanel", () => {
  it("is a labelled alertdialog with focus on the confirm button", () => {
    setup();
    const dialog = screen.getByRole("alertdialog", { name: "Delete Acme Pty Ltd?" });
    expect(dialog).toHaveAccessibleDescription("It is removed from your list.");
    expect(screen.getByRole("button", { name: "Yes, delete" })).toHaveFocus();
  });

  it("confirms on click and cancels on Escape or Cancel", async () => {
    const { onConfirm, onCancel } = setup();
    await userEvent.keyboard("{Enter}");
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("disables the confirm button while busy", () => {
    setup(true);
    expect(screen.getByRole("button", { name: "Yes, delete" })).toBeDisabled();
  });
});
