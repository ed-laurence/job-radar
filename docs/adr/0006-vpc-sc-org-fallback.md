# ADR-0006: VPC-SC when an Organization exists; deny-all + Private Google Access otherwise

- Status: accepted
- Date: 2026-11-25

## Context
Sandbox jobs (agents, test-runner) and the red team run untrusted or semi-trusted code. They must reach only this project's Vertex, GCS and Model Armor. VPC Service Controls give that guarantee, but an access policy needs a Google Cloud Organization, which a plain personal project lacks.

## Decision
`bootstrap.sh` checks for an Organization and offers Cloud Identity Free when there's none. OpenTofu then branches on `org_id`:

- **`org_id` set:**
  - `modules/vpc_sc` creates a project-scoped access policy and a perimeter. Restricted services include Vertex, GCS, Model Armor, SDP, KMS, Secret Manager, Cloud SQL Admin, Run, Logging and Monitoring, and VPC-accessible services are limited to the same list.
  - The deploy and review service accounts get an ingress rule.
  - Private DNS resolves `*.googleapis.com` and `*.run.app` to `restricted.googleapis.com` (199.36.153.4/30).
- **`org_id` null (fallback, only if Organization setup fails and the owner agrees):**
  - No perimeter.
  - Tagged jobs get a deny-all egress firewall. The only exception is `private.googleapis.com` (199.36.153.8/30) on 443, reached through Private Google Access and private DNS.
  - IAM is granted in this project only, and no service account has roles in other projects.

## Consequences (residual risk of the fallback)
- `private.googleapis.com` serves **every** Google API, in **any** project. A sandboxed process could send data out through an API it has credentials for in another project, for example writing to an attacker-owned GCS bucket that grants `allUsers` write.
- This is partly mitigated:
  - The sandbox's only Google credential is its job service account's metadata token, which holds Vertex plus its own GCS prefix, and nothing in other projects.
  - Public-write buckets in other projects accept anonymous writes without a token. Data written that way would be only what the sandbox can see: seed data and the repo, with no secrets or user data.
  - The Publisher scan and the redaction filter still apply.
- Without the perimeter, a stolen token is usable from outside the VPC.
- Accepted for a personal reference build. With an Organization, use VPC-SC.

## Revisit when
An Organization becomes available: set `org_id`, apply with `vpc_sc_dry_run = true`, review the violation logs, then enforce.
