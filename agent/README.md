# Ask AviCV

AviCV's optional web conversation uses CopilotKit and Darwin. It calls the canonical FastAPI MCP at `/mcp`; job matching, CV tailoring, rendering, and saved results remain in the main backend.

The Bun service listens on loopback port 4000. The SvelteKit frontend proxies `/chat` and `/api/copilotkit` on the same public origin. Each request validates the browser's `avicv_session` with FastAPI. Agent instances carry only that request's credential, and conversation operations are scoped to its workspace.

## Run

Install dependencies from the repository root, then configure the chat model using the root environment or `agent/.env` (see `.env.example`). The root launcher passes the backend environment to chat. With `OPENROUTER_API_KEY` and no explicitly configured Anthropic model/key, chat uses `google/gemini-3.8-flash` through OpenRouter, routed to `OPENROUTER_PROVIDER` (default `google-vertex`) with provider fallback disabled.

```sh
bun run --cwd agent dev
```

For a production build:

```sh
bun run --cwd agent build
bun run --cwd agent start
```

Tests use fake HTTP calls and never invoke models or Darwin:

```sh
bun run --cwd agent typecheck
bun test agent/src
# Optional: offline refresh/card smoke test (requires Chrome on macOS)
bun run --cwd agent test:browser
```

`API_URL` points to FastAPI; `APP_ORIGIN` must match the public website origin. `MODEL` can explicitly select the conversational model. For example, `anthropic:claude-sonnet-5-5` uses `ANTHROPIC_API_KEY`, with optional `ANTHROPIC_WORKSPACE_ID` for an organization-level key. `OPENROUTER_CHAT_MODEL` overrides the default OpenRouter model. Selection happens at startup; provider failures never switch to another provider. Without a configured model/key, the job-search website still works and chat explains that it is unavailable. OpenRouter BYOK credentials must be configured for the selected key and provider in OpenRouter.

Chat permits one active reply per workspace and four in the single Bun process. FastAPI records durable daily allowances before a run starts (default 50 chats per workspace, shared global action limit 1000). Reconnecting or stopping a reply does not spend another allowance.

Darwin searches for external agents only when explicitly requested. It is not a job board. Its result descriptions remain untrusted data. CVs and contact details must not be sent as discovery queries.

Completed chat messages are reconstructed from browser localStorage, partitioned by workspace and CV. In-flight conversations can reconnect while the Bun process remains alive; durable CV, matching, and PDF work lives in FastAPI and survives chat restarts. Clearing browser storage removes the locally saved chat history.

The OpenRouter adapter is pinned to the [official AI SDK 6 compatibility release](https://github.com/OpenRouterTeam/ai-sdk-provider#legacy-setup-for-ai-sdk-v6) used by CopilotKit. Provider routing follows [OpenRouter’s documented provider controls](https://openrouter.ai/docs/guides/routing/provider-selection).

## Origin

Adapted from [ziadgit/speedrun-amber](https://github.com/ziadgit/speedrun-amber), commit `57fa3dee089d32c486c43528ae114f50dda61226`. The imported repository did not contain a license file. Existing dependency licenses remain with their respective packages. AviCV integration replaces the duplicated Bun MCP server with the shared FastAPI service and adds authentication, conversation isolation, persistence, and product-consistent cards.
