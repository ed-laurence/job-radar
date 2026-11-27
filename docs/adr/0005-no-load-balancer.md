# ADR-0005: No load balancer or Cloud Armor; serve from Cloud Run run.app URLs

- Status: accepted
- Date: 2026-11-25

## Context
The app has exactly two provisioned accounts, sign-up is disabled in Identity Platform, and every route except sign-in and `/healthz` needs an allowlisted, verified JWT. A global external Application Load Balancer with Cloud Armor costs a fixed monthly amount whether or not anyone uses the app. It would also need a custom domain, which means a DNS step and an owner domain in config, and that domain is a sensitive identifier.

## Decision
- `web` is served on its `run.app` URL. It is the only service with `allUsers` as `run.invoker`, and `infra/policy/no_public_iam.rego` enforces that.
- `api` is IAM-protected (invokers: `web`, the red team and the uptime checker). `coordinator` uses internal-only ingress.
- Abuse controls live in the app and the platform: per-user and per-IP token buckets in Postgres, stricter limits on AI and agent routes, body-size limits, and Cloud Run `max-instances` caps. Model Armor screens content.
- No Cloud Armor and no load balancer.

## Consequences
- No WAF rules, no managed DDoS policies and no IP allow or deny lists at the edge. Cloud Run's front end still absorbs volumetric traffic.
- `max-instances` caps limit the cost of a traffic spike. They don't stop one, so a spike can make the app unavailable.
- Google-managed TLS on `run.app`, with no certificates to manage.
- Webhooks can't call `api` directly. They need a path through `web` or a later public endpoint (see the handoff's open questions).

## How to add it later
1. Add a serverless NEG per public service and a global external Application Load Balancer with a managed certificate for a custom domain.
2. Attach a Cloud Armor security policy (preconfigured WAF rules, rate-based ban, optional reCAPTCHA).
3. Set `web` (and any public `api` path) to `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` and set `default_uri_disabled = true`, so `run.app` can't bypass the load balancer.
4. Add the domain to Identity Platform `authorized_domains` and to the denylist.
5. Add a policy rule requiring LB-only ingress on public services.
