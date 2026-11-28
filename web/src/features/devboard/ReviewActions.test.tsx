import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ReviewActions } from "./ReviewActions";

function renderActions(overrides: Partial<Parameters<typeof ReviewActions>[0]> = {}) {
  const onMarkDone = vi.fn();
  const onRequestChanges = vi.fn();
  render(
    <ReviewActions
      pending={false}
      onMarkDone={onMarkDone}
      onRequestChanges={onRequestChanges}
      {...overrides}
    />,
  );
  return { onMarkDone, onRequestChanges };
}

describe("ReviewActions", () => {
  it("marks the card done", async () => {
    const { onMarkDone } = renderActions();
    await userEvent.setup().click(screen.getByRole("button", { name: "Mark done" }));
    expect(onMarkDone).toHaveBeenCalledOnce();
  });

  it("requires notes before requesting changes, and sends them trimmed", async () => {
    const { onRequestChanges } = renderActions();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Request changes" }));
    const notes = screen.getByRole("textbox", { name: /Notes for the agent/ });
    expect(notes).toBeRequired();
    const send = screen.getByRole("button", { name: "Send back for changes" });
    expect(send).toBeDisabled();
    await user.type(notes, "   ");
    expect(send).toBeDisabled();
    await user.type(notes, "Handle the empty list. ");
    await user.click(send);
    expect(onRequestChanges).toHaveBeenCalledWith("Handle the empty list.");
  });

  it("cancels the request without sending anything", async () => {
    const { onRequestChanges } = renderActions();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Request changes" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(onRequestChanges).not.toHaveBeenCalled();
  });

  it("disables the actions while a move is pending and shows the error", async () => {
    renderActions({ pending: true, error: "Can't move it. The branch is gone." });
    expect(screen.getByRole("button", { name: "Mark done" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("The branch is gone.");
  });
});
