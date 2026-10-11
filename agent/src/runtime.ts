import { createOpenRouter } from "@openrouter/ai-sdk-provider";
import { createAnthropic } from "@ai-sdk/anthropic";
import { BuiltInAgent, CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";
import { chatConfigured, type Config } from "./config";
import type { Session } from "./auth";
import { createDarwinProvider } from "./darwin";
import { AGENT_INSTRUCTIONS } from "./instructions";
import type { ChatLease } from "./admission";
import { WorkspaceRunner } from "./scoped-runner";

export function agentModel(config: Config, fetchImpl?: typeof fetch) {
  const model = config.model!;
  const sep = model.search(/[:/]/);
  if (sep >= 0 && model.slice(0, sep).toLowerCase() === "anthropic") {
    return createAnthropic({ apiKey: config.anthropicApiKey, ...(config.anthropicWorkspaceId ? { headers: { "anthropic-workspace-id": config.anthropicWorkspaceId } } : {}), fetch: fetchImpl })(model.slice(sep + 1));
  }
  if (sep >= 0 && model.slice(0, sep).toLowerCase() === "openrouter") {
    return createOpenRouter({ apiKey: config.openrouterApiKey, fetch: fetchImpl })(model.slice(sep + 1), {
      provider: { order: [config.openrouterProvider], allow_fallbacks: false },
    });
  }
  return model;
}

/** Scoped fetch cannot send the browser credential to redirects or partner servers. */
export function avicvMcpFetch(config: Config, session: Session, fetchFn: typeof fetch = fetch): typeof fetch {
  const endpoint = `${config.apiUrl}/mcp`;
  return (async (input: string | URL | Request, init?: RequestInit) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url !== endpoint) throw new Error("Unexpected AviCV MCP destination");
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
    headers.delete("authorization");
    headers.set("Cookie", session.cookie);
    headers.set("Origin", config.appOrigin);
    return fetchFn(input, { ...init, headers, redirect: "error" });
  }) as typeof fetch;
}

/** A fresh agent per authenticated request; no visitor credential is cached globally. */
export function createCopilotHandler(config: Config, session: Session, reservation?: ChatLease) {
  if (!chatConfigured(config)) throw new Error("MODEL and its API key are required to start chat");
  const darwin = createDarwinProvider(config.darwinMcpUrl);
  const agent = new BuiltInAgent({
    model: agentModel(config), prompt: AGENT_INSTRUCTIONS,
    maxSteps: 8, maxOutputTokens: 2500, overridableProperties: [],
    forwardSystemMessages: false, forwardDeveloperMessages: false,
    mcpClients: [darwin],
    mcpServers: [{ type: "http", name: "avicv", url: `${config.apiUrl}/mcp`, options: { fetch: avicvMcpFetch(config, session) } }],
  });
  const runtime = new CopilotRuntime({ agents: { default: agent }, runner: new WorkspaceRunner(session.workspaceId, undefined, () => darwin.close(), reservation) });
  return createCopilotRuntimeHandler({ runtime, basePath: "/api/copilotkit" });
}
