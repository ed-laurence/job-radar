import type { Metadata } from "next";
import { SignInScreen } from "@/features/session";

export const metadata: Metadata = { title: "Sign in · Job Radar" };

export default function SignInPage() {
  return <SignInScreen />;
}
