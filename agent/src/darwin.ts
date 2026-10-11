import { createMCPClient, type MCPClient } from "@ai-sdk/mcp";

/** Darwin's other tools have top-level oneOf/anyOf schemas that Anthropic rejects. */
export const DARWIN_TOOLS = ["search"] as const;

type DarwinTools = Awaited<ReturnType<MCPClient["tools"]>>;

export type DarwinDeps = {
  createClient?: (url: string) => Promise<Pick<MCPClient, "tools" | "close">>;
};

const defaultCreateClient = (url: string) => createMCPClient({ transport: { type: "http", url } });

/** An `mcpClients` entry exposing only DARWIN_TOOLS; a Darwin outage yields no tools, not a failed run. */
export function createDarwinProvider(url: string, deps: DarwinDeps = {}) {
  const createClient = deps.createClient ?? defaultCreateClient;
  let client: Awaited<ReturnType<typeof createClient>> | undefined;
  return {
    async close() { const current = client; client = undefined; await current?.close(); },
    async tools(): Promise<DarwinTools> {
      try {
        client ??= await createClient(url);
        const all = await client.tools();
        const picked: DarwinTools = {};
        for (const name of DARWIN_TOOLS) if (all[name]) picked[name] = all[name];
        return picked;
      } catch (err) {
        console.error(`darwin: tools unavailable (${err instanceof Error ? err.message : String(err)})`);
        const stale = client;
        client = undefined;
        await stale?.close().catch(() => {});
        return {};
      }
    },
  };
}
