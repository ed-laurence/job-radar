# Follow-up drafts

`POST /roles/{id}/followups/draft` picks up to three people to follow up with after applying, and drafts a short message to each. It streams the drafts as Server-Sent Events.
- Only roles at `applied`, `interview` or `offer` qualify (422 otherwise). That's checked before the stream starts, so it's a plain problem response.
- The model (feature `followup`, on the open-model route with the Claude fallback) sees the listing, the ad and the user's own contacts at the company, all as delimited data blocks.
- Code enforces what the model can get wrong. A named person must appear in the ad, a contact must be the user's own, and an email must be in the ad. A message that sells, uses placeholders or stock phrases is flagged in the draft's `problems`.
- Each run replaces the role's earlier suggested drafts.

## How it streams
- Each draft is released as `segment` events (part `followup`) after it passes the redaction filter: secret patterns, the denylist, PII, then the output guard. A draft opens with a `To:` line naming the person and channel, then says why them, then gives the message.
- A segment flagged after release is replaced by a `retract` event with `[redacted]`.
- The stream ends with `done` (`result_id` is the first draft, `status` is the number of drafts, for example `1 drafts`) or an `error` event carrying an RFC 9457 problem.
