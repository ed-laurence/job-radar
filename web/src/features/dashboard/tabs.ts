import type { Schemas } from "@/lib/api";

type RoleStatus = Schemas["RoleStatus"];
type RoleStage = Schemas["RoleStage"];
type Sort = "rank" | "-found_at" | "-score" | "-posted_at";

export interface TabDef {
  label: string;
  /** Key in `JobSummary.tab_counts`. */
  count: keyof Schemas["StatusTabCounts"];
  status: readonly RoleStatus[];
  stage: readonly RoleStage[];
  sort: Sort;
  empty: string;
}

/** Stages a role can be acted on in (not filtered out, a duplicate, or in error). */
const LIVE: readonly RoleStage[] = ["new", "screened", "scored", "ready"];

/** The status tabs, as on job-seeker's main page. Filtered is shown only when it has roles. */
export const TABS = {
  act: {
    label: "Act now",
    count: "act_now",
    status: ["new", "reviewing"],
    stage: LIVE,
    sort: "-score",
    empty: "Nothing to act on. New roles appear here once they're scored.",
  },
  applied: {
    label: "Applied",
    count: "applied",
    status: ["applied"],
    stage: [],
    sort: "-posted_at",
    empty: "No applications waiting on a reply.",
  },
  interviewing: {
    label: "Interviewing",
    count: "interviewing",
    status: ["interview", "offer"],
    stage: [],
    sort: "-posted_at",
    empty: "No interviews in progress.",
  },
  passed: {
    label: "Passed",
    count: "passed",
    status: ["passed", "closed"],
    stage: [],
    sort: "-posted_at",
    empty: "No roles passed on or closed.",
  },
  rejected: {
    label: "Rejected",
    count: "rejected",
    status: ["rejected"],
    stage: [],
    sort: "-posted_at",
    empty: "No rejections recorded.",
  },
  unknown: {
    label: "Unknown",
    count: "unknown",
    status: ["unknown"],
    stage: [],
    sort: "-posted_at",
    empty: "No applications without a response.",
  },
  filtered: {
    label: "Filtered",
    count: "filtered",
    status: [],
    stage: ["filtered"],
    sort: "-found_at",
    empty: "No roles were filtered out.",
  },
} as const satisfies Record<string, TabDef>;

/** A status tab: a filtered list of role cards. */
export type Tab = keyof typeof TABS;
export const TAB_ORDER = Object.keys(TABS) as Tab[];

/** Tabs that show a panel instead of role cards. Campaign is a tab only on narrow screens. */
export const PANELS = {
  outreach: { label: "Outreach", title: "People to message: drafted, sent and replied" },
  feedback: {
    label: "Feedback",
    title: "Rejections: ask why, record the reasons, learn from them",
  },
  campaign: { label: "Campaign", title: "This week's plan, tasks and activity log" },
} as const;
export type Panel = keyof typeof PANELS;

/** Everything in the Status group. */
export type View = Tab | Panel;

export function isRoleTab(view: View): view is Tab {
  return view in TABS;
}

/** Tabs to show: Filtered only when it has roles; Campaign only when there's no campaign column. */
export function visibleTabs(counts: Schemas["StatusTabCounts"] | undefined, wide = false): View[] {
  const tabs: View[] = TAB_ORDER.filter((t) => t !== "filtered" || (counts?.filtered ?? 0) > 0);
  tabs.push("outreach", "feedback");
  if (!wide) tabs.push("campaign");
  return tabs;
}

export function isActNow(status: RoleStatus): boolean {
  return status === "new" || status === "reviewing";
}
