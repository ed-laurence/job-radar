import { redirect } from "next/navigation";

/** The Pipeline tab was removed; old links go to the Dashboard. */
export default function Page() {
  redirect("/dashboard");
}
