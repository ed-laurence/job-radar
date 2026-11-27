import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ResearchForm } from "./ResearchForm";
import { ResearchProgress } from "./ResearchProgress";

describe("ResearchForm", () => {
  it("has no target picker when the caller fixes the target, and starts in spec focus order", async () => {
    const onStart = vi.fn();
    const onFocusChange = vi.fn();
    render(
      <ResearchForm
        focus={["news", "overview"]}
        onFocusChange={onFocusChange}
        running={false}
        onStart={onStart}
      />,
    );
    expect(screen.queryByRole("combobox")).toBeNull();
    const group = screen.getByRole("group", { name: "Focus" });
    await userEvent.click(within(group).getByRole("checkbox", { name: "Hiring signals" }));
    expect(onFocusChange).toHaveBeenCalledWith(["news", "overview", "hiring_signals"]);
    await userEvent.click(screen.getByRole("button", { name: "Start research" }));
    expect(onStart).toHaveBeenCalledWith(["overview", "news"]);
  });

  it("offers a labelled target picker and locks while running", async () => {
    const onTargetChange = vi.fn();
    render(
      <ResearchForm
        targets={[
          { id: "t1", name: "Acme Pty Ltd" },
          { id: "t2", name: "Globex" },
        ]}
        targetId="t1"
        onTargetChange={onTargetChange}
        focus={["overview"]}
        onFocusChange={() => {}}
        running
        onStart={() => {}}
      />,
    );
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Employer" }), "t2");
    expect(onTargetChange).toHaveBeenCalledWith("t2");
    expect(screen.getByRole("button", { name: "Start research" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Overview" })).toBeDisabled();
  });
});

describe("ResearchProgress", () => {
  it("announces progress, lists steps and shows the sanitized summary with sources", () => {
    const { rerender } = render(
      <ResearchProgress running finished={false} steps={[]} summary={undefined} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Research running…");
    rerender(
      <ResearchProgress
        running={false}
        finished
        steps={[
          {
            id: "00000000-0000-4000-8000-000000000201",
            run_id: "00000000-0000-4000-8000-0000000000f1",
            seq: 0,
            kind: "tool_call",
            tool: "web_search",
            summary: "Searching recent news",
            at: "2026-11-25T01:00:00Z",
          },
        ]}
        summary={"Acme is **hiring**.<script>alert(1)</script>"}
        sources={["https://example.test/news"]}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Research finished.");
    const steps = screen.getByRole("list", { name: "Research steps" });
    expect(within(steps).getByText("Tool call · web_search")).toBeInTheDocument();
    const summary = screen.getByRole("region", { name: "Research summary" });
    expect(within(summary).getByText("hiring").tagName).toBe("STRONG");
    expect(summary.querySelector("script")).toBeNull();
    expect(within(summary).getByText("https://example.test/news")).toBeInTheDocument();
  });

  it("shows start and stream failures with their reference", () => {
    render(
      <ResearchProgress
        running={false}
        finished={false}
        steps={null}
        startError={{
          title: "Too many requests",
          description: undefined,
          correlationId: "corr-test-0001",
          fieldErrors: [],
        }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Too many requests");
    expect(screen.getByRole("alert")).toHaveTextContent("Reference: corr-test-0001");
    expect(screen.queryByRole("list", { name: "Research steps" })).toBeNull();
  });
});

describe("ResearchProgress: scrolling conversation window", () => {
  const step = (seq: number) => ({
    id: `00000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
    run_id: "00000000-0000-4000-8000-0000000000f1",
    seq,
    kind: "thought" as const,
    summary: `Step ${seq}`,
    at: "2026-11-25T01:00:00Z",
  });

  /** jsdom has no layout: give the window a fixed viewport and a height that grows with steps. */
  function fakeLayout(el: HTMLElement) {
    Object.defineProperty(el, "clientHeight", { configurable: true, get: () => 100 });
    Object.defineProperty(el, "scrollHeight", {
      configurable: true,
      get: () => el.querySelectorAll("li").length * 60,
    });
  }

  it("puts steps in a scrollable window and follows new steps to the end", async () => {
    const { rerender } = render(<ResearchProgress running finished={false} steps={[step(1)]} />);
    const win = screen.getByRole("region", { name: "Research conversation" });
    fakeLayout(win);
    rerender(<ResearchProgress running finished={false} steps={[step(1), step(2), step(3)]} />);
    await vi.waitFor(() => expect(win.scrollTop).toBe(180));
  });

  it("stops following when the reader scrolls up, and resumes at the bottom", async () => {
    const steps = [step(1), step(2), step(3)];
    const { rerender } = render(<ResearchProgress running finished={false} steps={steps} />);
    const win = screen.getByRole("region", { name: "Research conversation" });
    fakeLayout(win);

    win.scrollTop = 0; // reader scrolls up to an early step
    win.dispatchEvent(new Event("scroll"));
    rerender(<ResearchProgress running finished={false} steps={[...steps, step(4)]} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(win.scrollTop).toBe(0); // not yanked back down

    win.scrollTop = 240 - 100; // back at the bottom
    win.dispatchEvent(new Event("scroll"));
    rerender(<ResearchProgress running finished={false} steps={[...steps, step(4), step(5)]} />);
    await vi.waitFor(() => expect(win.scrollTop).toBe(300));
  });
});
