import type { NavItem } from "@/components/AppShell";
import type { Schemas } from "@/lib/api";

interface AppNavItem extends NavItem {
  maintainerOnly?: boolean;
}

export const NAV_ITEMS: readonly AppNavItem[] = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/roles", label: "Roles" },
  { href: "/contacts", label: "Contacts" },
  { href: "/employers", label: "Employers" },
  { href: "/dev-board", label: "Dev board" },
  { href: "/settings", label: "Settings" },
];

/** Hiding the link is a convenience; the API enforces the role (SEC-02). */
export function navFor(role: Schemas["AppRole"]): NavItem[] {
  return NAV_ITEMS.filter((item) => !item.maintainerOnly || role === "maintainer").map(
    ({ href, label }) => ({ href, label }),
  );
}
