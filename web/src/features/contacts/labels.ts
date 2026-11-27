import type { Schemas } from "@/lib/api";
import type { Contact } from "./api";

export type ContactStatus = Schemas["ContactStatus"];
export type Closeness = Schemas["Closeness"];

export const STATUS_LABELS: Record<ContactStatus, string> = {
  new: "New",
  drafted: "Drafted",
  sent: "Sent",
  replied: "Replied",
  skipped: "Skipped",
};
export const STATUSES = Object.keys(STATUS_LABELS) as ContactStatus[];

export const CLOSENESS_LABELS: Record<Closeness, string> = {
  worked: "Worked together",
  know: "Know them",
  barely: "Barely know them",
};
export const CLOSENESS = Object.keys(CLOSENESS_LABELS) as Closeness[];

export function fullName(c: Pick<Contact, "first_name" | "last_name">): string {
  return [c.first_name, c.last_name].filter(Boolean).join(" ") || "Unnamed contact";
}
