import { redirect } from "next/navigation";

/** The Analytics page was folded into the Health panel and the Dashboard. */
export default function Page() {
  redirect("/dashboard");
}
