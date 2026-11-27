# Role chat (streamed)

`/api/roles/{id}/chat/messages` holds a conversation about one role.

- **Send** (`POST`) saves the user's message and streams the reply (feature `chat`). It uses the same `segment` / `retract` / `done` / `error` events as kits, and `done.result_id` is the stored assistant message.
- **What the model sees:** the user's message is their own request, so it goes in the instructions. The listing, the ad, the assessment, the kit and the last 40 turns go in as data blocks.
- **Storage:** only the filtered reply is stored. If the stream fails, no assistant message is saved.
- **List and clear:** `GET` lists messages oldest first (cursor pages). `DELETE` clears the conversation.
- **Concurrency:** one reply per role at a time (409).
