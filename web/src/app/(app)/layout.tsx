import type { ReactNode } from "react";
import { SignedInFrame } from "./SignedInFrame";

export default function SignedInLayout({ children }: { children: ReactNode }) {
  return <SignedInFrame>{children}</SignedInFrame>;
}
