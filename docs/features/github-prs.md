# GitHub PRs for agent branches (`CODE_HOST=github`)

With `CODE_HOST=github`, a dev run that passes the Publisher checks is pushed to GitHub, and
`.github/workflows/agent-pr.yml` opens the PR. `CODE_HOST=local` (the default) keeps
everything in the scratch remote, as before.

## Flow
1. **Snapshot from GitHub `main`.** The agent's base is the repo's `main` on GitHub, never your
   working tree. Uncommitted local changes don't reach agent PRs. The runner fetches `main` with
   the deploy key into the scratch remote (`refs/remotes/github/main`) and bundles that commit.
2. **Dev run, publish to the scratch remote, Publisher checks**, as today.
3. **Publish commit.** If the checks pass, trusted code adds one commit on top of the agent's
   head with an unchanged tree (`git commit-tree`, run against the scratch remote).
   - Author and committer: `jobradar-publisher <publisher@example.test>`.
   - Message: `jobradar: publish task-<task uuid>`, followed by the trailers `agent-pr.yml`
     reads:
     ```
     Jobradar-Task: task-<task uuid>
     Jobradar-Verdict: pass
     Jobradar-Tests: <passed>/<passed+failed>
     Jobradar-Diff-Lines: <added+deleted, excluding regenerated code>
     ```
4. **Push.** The publish commit is pushed to `agent/task-<task uuid>` (`github_branch(task_id)`).
   There is one GitHub branch per card. A rework run starts from the card's head, so its push
   fast-forwards and the same PR updates. Force-pushes are never used: a non-fast-forward push
   fails the run.
   - The push uses `GIT_SSH_COMMAND="ssh -i <deploy key> -o IdentitiesOnly=yes -o
     UserKnownHostsFile=<pinned github.com key> -o StrictHostKeyChecking=yes"`.
   - It runs with no global git config, and error text holds only the verb and exit code.
   - A failed push fails the run. The scratch branch stays, so the card can be retried.
5. **PR.** The push triggers `ci.yml` and `agent-pr.yml`. The workflow opens the PR with
   `GITHUB_TOKEN`; its body comes from the trailers only.
6. **PR sync (local poller).** Every `PR_SYNC_INTERVAL_S` (60), for live cards whose branch was
   pushed (column in `agent_review`, `changes_requested`, `agent_dev`, `human_review`):
   - The poller calls `PullRequests.find(github_branch(task.id))`. The adapter runs
     `gh pr list --repo <GITHUB_REPO> --head <branch> --state all --json
     url,number,state,mergedAt --limit 1` using your gh login, read-only.
   - It stores `pr_url` on the card.
   - **Merged** and the card is in `human_review` → a system move to `done`.
   - **Closed without merging** → the card gets a `pr-closed` label (shown on the card); the card
     doesn't move.
   - If gh is missing or not signed in, the sync is skipped. It logs once and fails no card.

## Settings (env)
| Name | Default | Meaning |
|---|---|---|
| `CODE_HOST` | `local` | `github` turns on steps 1, 3–6 |
| `GITHUB_REPO` | (none; required for `github`) | `owner/name` |
| `GITHUB_DEPLOY_KEY_PATH` | `~/.config/jobradar/github_deploy_key` | private key, mode 600, never in the repo |
| `GITHUB_KNOWN_HOSTS_PATH` | `~/.config/jobradar/github_known_hosts` | pinned github.com host key |
| `PR_SYNC_INTERVAL_S` | `60` | poll interval; 0 turns it off |

## One-time setup (`scripts/github-setup.sh`, run by the human)
- Generates an ed25519 key at `GITHUB_DEPLOY_KEY_PATH`, unless one already exists.
- Writes the pinned github.com host key.
- Registers the public key on the repo with write access: `gh repo deploy-key add --allow-write`.
- Adds a ruleset on `main`: a PR is required, force pushes and deletion are blocked, and
  repository admins can bypass. The deploy key then can't push to `main`; agents push only to
  `agent/*`.
- Allows GitHub Actions to create PRs (`agent-pr.yml` uses `GITHUB_TOKEN`). Default workflow
  permissions stay read-only, since each workflow declares its own.
- It is idempotent, prints what it does, and prints no key material.
