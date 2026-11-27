import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";

const NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/roles", label: "Roles" },
];

function renderShell(onSignOut = vi.fn()) {
  render(
    <AppShell
      navItems={NAV}
      currentPath="/roles/abc"
      accountLabel="maintainer"
      onSignOut={onSignOut}
    >
      <h1>Roles</h1>
    </AppShell>,
  );
  return onSignOut;
}

describe("AppShell", () => {
  it("has banner, labelled navigation and main landmarks", () => {
    renderShell();
    expect(screen.getByRole("banner")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(
      within(nav)
        .getAllByRole("link")
        .map((l) => l.textContent),
    ).toEqual(["Dashboard", "Roles"]);
    expect(screen.getByRole("main")).toContainElement(
      screen.getByRole("heading", { name: "Roles" }),
    );
  });

  it("marks the current section with aria-current", () => {
    renderShell();
    expect(screen.getByRole("link", { name: "Roles" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
  });

  it("starts with a skip link that targets main", async () => {
    renderShell();
    await userEvent.tab();
    const skip = screen.getByRole("link", { name: "Skip to main content" });
    expect(skip).toHaveFocus();
    expect(skip).toHaveAttribute("href", `#${screen.getByRole("main").id}`);
  });

  it("is a top bar: brand, main navigation and actions inside the banner", () => {
    render(
      <AppShell
        navItems={NAV}
        currentPath="/dashboard"
        actions={<button type="button">Health</button>}
        onSignOut={vi.fn()}
      >
        <h1>Dashboard</h1>
      </AppShell>,
    );
    const banner = screen.getByRole("banner");
    expect(within(banner).getByRole("link", { name: "Job Radar" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    // The radar mark sits left of the name and is decorative.
    const brand = within(banner).getByRole("link", { name: "Job Radar" });
    const mark = brand.querySelector("svg");
    expect(mark).not.toBeNull();
    expect(mark).toHaveAttribute("aria-hidden", "true");
    expect(brand.firstElementChild).toBe(mark);
    expect(within(banner).getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(within(banner).getByRole("button", { name: "Health" })).toBeInTheDocument();
    expect(within(banner).getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("signs out from a real button", async () => {
    const onSignOut = renderShell();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });
});
