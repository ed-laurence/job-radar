import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

function html(md: string): string {
  const { container } = render(<Markdown>{md}</Markdown>);
  return container.innerHTML;
}

describe("Markdown", () => {
  it("renders ordinary markdown", () => {
    render(<Markdown>{"## Summary\n\n- **Strong** fit\n- Remote"}</Markdown>);
    expect(screen.getByRole("heading", { level: 2, name: "Summary" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("never renders raw HTML from the model", () => {
    const out = html(
      'Hi <script>alert(1)</script><img src=x onerror="alert(1)"><b onclick="x">b</b>',
    );
    expect(out).not.toMatch(/<script|onerror|onclick|<img/i);
  });

  it("strips javascript: and data: links", () => {
    html("[a](javascript:alert(1)) [b](data:text/html,x)");
    for (const link of screen.queryAllByRole("link")) {
      expect(link.getAttribute("href") ?? "").not.toMatch(/^(javascript|data):/i);
    }
  });

  it("drops images (no tracking pixels)", () => {
    expect(html("![x](https://tracker.example.test/p.gif)")).not.toContain("<img");
  });

  it("opens safe links with no referrer", () => {
    render(<Markdown>{"[docs](https://example.test/docs)"}</Markdown>);
    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("href", "https://example.test/docs");
    expect(link).toHaveAttribute("rel", "noopener noreferrer nofollow");
  });
});
