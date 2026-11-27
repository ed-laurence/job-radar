/**
 * One-off: deletes the owner's leftover e2e data (roles, contacts and tasks whose title or first
 * name starts with "E2E"), left by runs from before the specs cleaned up after themselves.
 * Needs scripts/dev.sh running and E2E_OWNER_EMAIL / E2E_OWNER_PASSWORD (environment or
 * ~/.config/jobradar/local.env): it signs the owner in through the real sign-in page. Run from web/:
 *
 *   corepack pnpm exec tsx tests/e2e/support/purge.ts            # when tsx is installed
 *
 * Without tsx (no new dependency), compile with the repo's tsc and run with node:
 *
 *   corepack pnpm exec tsc tests/e2e/support/purge.ts --outDir "$TMPDIR/purge" --module commonjs \
 *     --moduleResolution node --target es2022 --skipLibCheck --types node
 *   node "$TMPDIR/purge/purge.js"           # add --dry-run to only list what would go
 */
import { api, deleteItem, type Kind } from "./cleanup";

const PREFIX = "E2E";
const MAX_PAGES = 100;

interface Item {
  id: string;
  version: number;
  title?: string;
  first_name?: string;
}

/** Every item of a kind (all pages), filtered by the server where it can (roles: `q`). */
async function listAll(kind: Kind): Promise<Item[]> {
  const items: Item[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < MAX_PAGES; page++) {
    const query = new URLSearchParams({ limit: "100" });
    if (kind === "roles") query.set("q", PREFIX);
    if (cursor) query.set("cursor", cursor);
    const res = await api(`/${kind}?${query}`);
    if (!res.ok) throw new Error(`purge: GET /${kind} answered ${res.status}`);
    const body = (await res.json()) as { items: Item[]; next_cursor?: string };
    items.push(...body.items);
    cursor = body.next_cursor;
    if (!cursor) break;
  }
  return items;
}

const say = (line: string) => process.stdout.write(`${line}\n`);
const warn = (line: string) => process.stderr.write(`${line}\n`);

const isE2E = (item: Item) => (item.title ?? item.first_name ?? "").startsWith(PREFIX);

async function main(): Promise<void> {
  const dryRun = process.argv.includes("--dry-run");
  const kinds: Kind[] = ["roles", "contacts", "tasks"];
  for (const kind of kinds) {
    const doomed = (await listAll(kind)).filter(isE2E);
    say(`${kind}: ${doomed.length} with the ${PREFIX} prefix`);
    if (dryRun) continue;
    // One at a time: the API's rate limit applies, and api() waits out any 429.
    let failed = 0;
    for (const item of doomed) {
      try {
        await deleteItem(kind, item.id, item.version);
      } catch (error) {
        failed++;
        warn(String(error));
      }
    }
    say(`${kind}: deleted ${doomed.length - failed}, failed ${failed}`);
    if (failed) process.exitCode = 1;
  }
}

main().catch((error: unknown) => {
  warn(String(error));
  process.exitCode = 1;
});
