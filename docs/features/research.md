# Company research agent

`POST /targets/{id}/research-runs` starts a run on a campaign target. It takes an optional `focus` (`overview`, `hiring_signals`, `people_to_approach`, `news`) and returns 202 with a `queued` run. The agent then runs in the background.

## How the agent works
- **Native tool use:** the agent calls the LLM port one turn at a time (`next_action`, feature `research`). Each turn the model either calls one tool or gives its final answer, which is validated against a fixed shape (summary, up to five findings, sources). The agent owns the loop, so every step is saved as it happens.
- **Tools:** `lookup_known_data` answers from the user's own records: the target's notes, roles tracked at the company, and contacts there. Its input is a strict schema (`topic`: `target`, `roles` or `contacts`), and an invalid input comes back to the model as an error result. That lets the agent run offline and under `LLM=replay`.
- **Web search:** off by default. With `RESEARCH_WEB_SEARCH=true` the model also gets the provider's own web-search tool (Claude `web_search_20250305`, the variant on Vertex). The app never runs a search itself. Each search shows up as a `tool_call` and `tool_result` step, and the URLs it returned can be cited as sources.
- **Untrusted results:** tool results are untrusted data. On every turn they're wrapped as data blocks and pass the input guard before they reach the model. A blocked result stops the run (`failed`) before the model sees it. The model's text and tool arguments pass the output guard.
- **Limits:** a run stops (`failed`, with a `Stopped: …` status step) after 6 model turns without an answer, or once its spend reaches $0.50. Each turn also checks the feature's daily budget and writes its own `llm_calls` row. The run's `cost_usd` adds up its turns.
- **Steps:** every model turn (`thought`), tool call, server-tool search and tool-result summary (`tool_result`), each finding, and the start and end (`status`) are redacted and saved. A step the filter can't clear shows only its kind.
- **Sources:** only URLs a tool or search actually returned are kept. The model can't add its own.

## Following a run
- `GET /research-runs/{id}` returns the status, summary, sources and cost.
- `GET /research-runs/{id}/steps` replays saved steps as `step` events (`id` is the step `seq`; send `Last-Event-ID` to resume), follows a live run, and ends with `done` (`status` is the run's status).
- Research steps live in the same `run_steps` table as dev-board agent steps (migration 0002 moved them out of `research_steps`). They're private to their owner. Both streams use one mechanism: an insert notifies the shared `run_steps` channel, which wakes the stream, and the stream re-reads from its last `seq`.

## Replay
Under `LLM=replay` each turn has its own fixture, keyed by a hash of the opening prompt, the tools and the whole transcript so far (`backend/tests/fixtures/llm/jobs/research/`). A turn that called a tool records the call.
