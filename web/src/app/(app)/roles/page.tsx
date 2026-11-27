import { parseAge, RolesPage } from "@/features/roles";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <RolesPage initialAge={parseAge(await searchParams)} />;
}
