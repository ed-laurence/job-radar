import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ComingSoon } from "./ComingSoon";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { LoadingState } from "./LoadingState";

describe("state components", () => {
  it("LoadingState is a polite status", () => {
    render(<LoadingState label="Loading roles" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading roles");
  });

  it("EmptyState is a labelled region with an optional action", () => {
    render(
      <EmptyState
        title="No roles yet"
        description="Add one."
        action={<button type="button">Add role</button>}
      />,
    );
    expect(screen.getByRole("region", { name: "No roles yet" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add role" })).toBeInTheDocument();
  });

  it("ErrorState is an alert showing only the correlation id, with retry", async () => {
    const onRetry = vi.fn();
    render(<ErrorState correlationId="corr-1" onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Reference: corr-1");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("ComingSoon names the page", () => {
    render(<ComingSoon title="Pipeline" />);
    expect(screen.getByRole("heading", { level: 1, name: "Pipeline" })).toBeInTheDocument();
    expect(screen.getByText("Coming soon.")).toBeInTheDocument();
  });
});
