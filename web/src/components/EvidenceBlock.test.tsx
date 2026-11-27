import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { EvidenceController, EvidenceTest } from "@/lib/evidence";
import { EvidenceBlock, type EvidenceBlockProps } from "./EvidenceBlock";

const TEST: EvidenceTest = {
  role_id: "00000000-0000-4000-8000-0000000000a1",
  verdict: "apply",
  verdict_text: "Passes the evidence test and scores 70+: apply.",
  shown: 2,
  total: 4,
  from_records: 1,
  items: [
    {
      requirement: "Data platform delivery",
      match: "shown",
      quote: "Led delivery of a lakehouse platform for a retail client.",
      source: "cv_technical",
      why: "Direct match on delivery.",
    },
    {
      requirement: "Stakeholder leadership",
      match: "shown",
      quote: "Ran the steering group for a platform rebuild.",
      source: "cv_leadership",
      why: "Leadership CV shows it.",
    },
    {
      requirement: "Working directly with clients",
      match: "shown",
      quote: "Weekly workshops with the client's analysts.",
      source: "records",
      record_id: "00000000-0000-4000-8000-0000000000e1",
      record_title: "Platform Architect at Example Co",
      why: "Only in your records.",
    },
    {
      requirement: "Strong SQL and data modelling",
      match: "partial",
      source: "none",
      why: "SQL is shown, modelling isn't.",
    },
    {
      requirement: "Kafka",
      match: "missing",
      source: "none",
      why: "Not mentioned anywhere.",
    },
  ],
  team_check: { status: "ok", text: "within twice what you've led." },
  checked_at: "2026-11-25T20:02:00Z",
  model: "replay",
};

const CONTEXTS = ["Platform Architect, Example Co", "Personal project", "Other"];

function controller(overrides: Partial<EvidenceController> = {}): EvidenceController {
  return {
    test: TEST,
    loading: false,
    loadError: undefined,
    onRetryLoad: vi.fn(),
    running: false,
    cooling: false,
    runError: undefined,
    onRun: vi.fn(),
    notice: "",
    timeZone: "Australia/Melbourne",
    doneFor: null,
    onDone: vi.fn(),
    onCancelDone: vi.fn(),
    saving: false,
    saveError: undefined,
    onSaveDone: vi.fn(),
    contexts: CONTEXTS,
    ...overrides,
  };
}

function renderBlock(props: Partial<EvidenceBlockProps> = {}) {
  const all: EvidenceBlockProps = { variant: "detail", ...controller(), ...props };
  render(<EvidenceBlock {...all} />);
  return all;
}

function items() {
  return within(screen.getByRole("list", { name: "Must-haves" })).getAllByRole("listitem");
}

describe("EvidenceBlock", () => {
  it("shows the verdict line, the verdict text, the team check and when it was checked", () => {
    renderBlock();
    const block = screen.getByRole("region", { name: "Evidence test" });
    expect(block).toHaveTextContent(
      "Apply · you show 2 of 4 must-haves (1 from your records, not on your CV)",
    );
    expect(block).toHaveTextContent(
      "Passes the evidence test and scores 70+: apply. Done something that's marked ≈ or ✗ but isn't on your CV? Click I've done this.",
    );
    expect(block).toHaveTextContent("Team: within twice what you've led.");
    expect(block).toHaveTextContent("Checked Thu, 26 Nov, 7:02 am");
    expect(within(block).getByText("Thu, 26 Nov, 7:02 am")).toHaveAttribute(
      "datetime",
      TEST.checked_at,
    );
  });

  it("renders each match with a hidden-from-AT mark and a text alternative", () => {
    renderBlock();
    const [cv, lead, rec, partial, missing] = items();
    for (const li of items()) {
      expect(li.querySelector("[aria-hidden='true']")).not.toBeNull();
    }
    expect(cv).toHaveTextContent("✓Data platform delivery (shown)");
    expect(partial).toHaveTextContent("≈Strong SQL and data modelling (partly shown)");
    expect(missing).toHaveTextContent("✗Kafka (missing)");
    expect(within(cv as HTMLElement).getByText("Data platform delivery").tagName).toBe("B");
    expect(lead).toBeDefined();
    expect(rec).toBeDefined();
  });

  it("shows the quote, the source chip, the record title and the why", () => {
    renderBlock();
    const [cv, lead, rec] = items() as [HTMLElement, HTMLElement, HTMLElement];
    expect(within(cv).getByText(/lakehouse platform/).tagName).toBe("BLOCKQUOTE");
    expect(within(cv).getByText("technical CV")).toBeInTheDocument();
    expect(within(cv).getByText("Direct match on delivery.")).toBeInTheDocument();
    expect(within(lead).getByText("leadership CV")).toBeInTheDocument();
    expect(within(rec).getByText("your records · not on CV yet")).toBeInTheDocument();
    expect(within(rec).getByText("Platform Architect at Example Co")).toBeInTheDocument();
  });

  it("offers I've done this only on partial and missing items", async () => {
    const props = renderBlock();
    const buttons = screen.getAllByRole("button", { name: /^I've done this/ });
    expect(buttons.map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual([
      "I've done this: Strong SQL and data modelling",
      "I've done this: Kafka",
    ]);
    await userEvent.click(screen.getByRole("button", { name: "I've done this: Kafka" }));
    expect(props.onDone).toHaveBeenCalledWith(expect.objectContaining({ requirement: "Kafka" }));
  });

  it("opens the entry-row editor in a dialog for the requirement, and saves the rows", async () => {
    const props = renderBlock({ doneFor: "Kafka" });
    const dialog = screen.getByRole("dialog", { name: "I've done this: Kafka" });
    const form = within(dialog).getByRole("form", { name: "Add Kafka to your records" });
    // The requirement comes from the gap, so only the rows are asked for.
    expect(within(form).queryByRole("textbox", { name: "Requirement it answers" })).toBeNull();
    const where = within(form).getByRole("combobox", { name: "Where" });
    expect(where).toHaveFocus();
    expect(
      within(where)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Choose the role or project…", ...CONTEXTS]);

    await userEvent.click(within(form).getByRole("button", { name: "Save and run again" }));
    expect(within(form).getByText("Add at least one role and what you did there")).toBeVisible();
    expect(props.onSaveDone).not.toHaveBeenCalled();

    await userEvent.selectOptions(where, "Platform Architect, Example Co");
    await userEvent.type(
      within(form).getByRole("textbox", { name: "What you did there" }),
      "Ran it",
    );
    await userEvent.click(within(form).getByRole("button", { name: "+ Another role" }));
    const second = within(form).getByRole("group", { name: "Entry 2" });
    await userEvent.selectOptions(within(second).getByRole("combobox", { name: "Where" }), "Other");
    await userEvent.type(
      within(second).getByRole("textbox", { name: "What you did there" }),
      " Ran a meetup  ",
    );
    await userEvent.click(within(form).getByRole("button", { name: "Save and run again" }));
    expect(props.onSaveDone).toHaveBeenCalledWith({
      requirement: "Kafka",
      entries: [
        { context: "Platform Architect, Example Co", detail: "Ran it" },
        { context: "Other", detail: "Ran a meetup" },
      ],
    });
    await userEvent.keyboard("{Escape}");
    expect(props.onCancelDone).toHaveBeenCalled();
  });

  it("shows a save failure inside the dialog", () => {
    renderBlock({
      doneFor: "Kafka",
      saveError: {
        title: "The record couldn't be saved",
        description: undefined,
        correlationId: undefined,
        fieldErrors: [],
      },
    });
    const dialog = screen.getByRole("dialog", { name: "I've done this: Kafka" });
    expect(within(dialog).getByRole("alert")).toHaveTextContent("The record couldn't be saved");
  });

  it("Run again asks for a new run", async () => {
    const props = renderBlock();
    await userEvent.click(screen.getByRole("button", { name: "Run again" }));
    expect(props.onRun).toHaveBeenCalledTimes(1);
  });

  it("disables the run while running, and while the AI limit cools down", () => {
    const { unmount } = render(
      <EvidenceBlock variant="detail" {...controller({ running: true })} />,
    );
    const busy = screen.getByRole("button", { name: "Running…" });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute("aria-busy", "true");
    unmount();
    render(
      <EvidenceBlock
        variant="detail"
        {...controller({
          cooling: true,
          runError: {
            title: "The AI is busy",
            description: "Wait",
            correlationId: undefined,
            fieldErrors: [],
          },
        })}
      />,
    );
    expect(screen.getByRole("button", { name: "Run again" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("The AI is busy");
    expect(screen.getByText("Wait a moment before running it again.")).toBeInTheDocument();
  });

  it("offers the first run when there's no evidence at all", async () => {
    const props = renderBlock({ test: null });
    expect(screen.getByText(/Evidence test not run yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Run the evidence test" }));
    expect(props.onRun).toHaveBeenCalled();
  });

  it("shows a load error with a retry", async () => {
    const props = renderBlock({
      test: undefined,
      summary: { must_haves_total: 4, shown: 3, from_records: 0, verdict: "consider", summary: "" },
      loadError: {
        title: "The evidence failed to load",
        description: undefined,
        correlationId: "corr-1",
        fieldErrors: [],
      },
    });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The evidence failed to load");
    expect(alert).toHaveTextContent("Reference: corr-1");
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(props.onRetryLoad).toHaveBeenCalled();
  });

  it("on a card, collapses to the summary line and reports when it opens", async () => {
    const onToggle = vi.fn();
    renderBlock({
      variant: "card",
      test: undefined,
      summary: { must_haves_total: 5, shown: 4, from_records: 1, verdict: "apply", summary: "" },
      onToggle,
    });
    const summary = screen.getByText(/you show 4 of 5 must-haves/);
    expect(summary.closest("details")).not.toHaveAttribute("open");
    await userEvent.click(summary);
    await vi.waitFor(() => expect(onToggle).toHaveBeenCalledWith(true));
  });
});
