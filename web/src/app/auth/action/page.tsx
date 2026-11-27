import type { Metadata } from "next";
import { EmailActionScreen } from "@/features/session";

/**
 * Identity Platform's email links (reset password, verify and recover email) land here. Public:
 * outside the signed-in (app) group. No referrer, so the one-time code never leaves the page.
 */
export const metadata: Metadata = {
  title: "Account · Job Radar",
  referrer: "no-referrer",
};

export default function EmailActionPage() {
  return <EmailActionScreen />;
}
