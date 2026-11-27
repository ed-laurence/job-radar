import { redirect } from "next/navigation";

/** Renamed to Employers; old links keep working. */
export default function Page() {
  redirect("/employers");
}
