import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { loadConfig } from "../src/config";

// Live network is allowed here only (invariant 4). Everything printed is data (invariant 5).
const QUERY = "find agents that search current job postings, e.g. backend engineer roles at fintech companies, and can start work";

function oneLine(value: unknown, max = 140): string {
  const text = typeof value === "string" ? value : JSON.stringify(value ?? "");
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

function resultsOf(structured: unknown): unknown[] {
  const agents = (structured as { response?: { agents?: unknown } } | undefined)?.response?.agents;
  return Array.isArray(agents) ? agents : [];
}

async function main(): Promise<void> {
  const config = loadConfig();
  const client = new Client({ name: "amber-darwin-probe", version: "0.0.0" });
  await client.connect(new StreamableHTTPClientTransport(new URL(config.darwinMcpUrl)));

  const { tools } = await client.listTools();
  console.log(`tools (${tools.length}):`);
  for (const tool of tools) {
    console.log(`- ${tool.name}`);
    console.log(`  inputSchema: ${JSON.stringify(tool.inputSchema)}`);
  }

  const search = tools.find((t) => t.name === "search");
  if (!search) throw new Error("Darwin exposes no `search` tool");
  const props = (search.inputSchema.properties ?? {}) as Record<string, unknown>;

  const args: Record<string, unknown> = {};
  if ("query" in props) args.query = QUERY;
  else if ("q" in props) args.q = QUERY;
  else throw new Error("search has no query/q argument; adapt the probe to the schema above");
  if ("filters" in props) args.filters = { requiresExecutableRoute: true };

  console.log(`\nsearch args: ${JSON.stringify(args)}`);
  const response = await client.callTool({ name: "search", arguments: args });
  if (response.isError) throw new Error(`search returned an error: ${oneLine(response.content, 400)}`);

  const results = resultsOf(response.structuredContent);
  if (results.length === 0) {
    console.log(`no agents in structuredContent; raw: ${oneLine(response.content, 600)}`);
  }
  let startable = 0;
  for (const raw of results) {
    const r = (raw ?? {}) as Record<string, unknown>;
    if (r.canStartThread === true) startable++;
    console.log(`- ${oneLine(r.name, 60)} | canStartThread=${String(r.canStartThread)} | ${oneLine(r.description)}`);
  }
  console.log(`\n${results.length} results, ${startable} with canStartThread: true`);
  await client.close();
}

main().catch((err) => {
  console.error(`darwin-probe failed: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
