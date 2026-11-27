# Application pipeline (drag and drop)

The pipeline board shows roles in columns by `status`. Within a column, cards are ordered by `rank`, a fractional base-62 key compared in byte order (`COLLATE "C"`), with ties broken by id, so the order is always total.

- **Move between columns:** `POST /roles/{id}/status` with `status`, `version` and an optional `rank` (default: the top of the new column).
  - It writes a `status` event.
  - Moving to `applied` the first time sets `applied_at`.
  - Moving to `rejected` creates the rejection record, with optional `rejected_at`, `channel` and `reason_categories`. Rejection details are refused with any other status.
- **Reorder within a column:** `POST /roles/{id}/rank` with the key the client computed between its new neighbours, plus `version`. It writes a `rank` event.
- **Rebalancing:** when no key fits above the top card, the server spreads that column's keys evenly and places the card first.
- **Tests:** `tests/property/jobs/test_role_rank_property.py` checks that the order stays total and the keys stay valid under random moves.
