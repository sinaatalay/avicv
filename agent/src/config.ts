export interface Config {
  apiUrl: string;
  model: string | undefined;
  darwinMcpUrl: string;
  appOrigin: string;
  port: number;
  anthropicWorkspaceId: string | undefined;
  anthropicApiKey: string | undefined;
  openrouterApiKey: string | undefined;
  openrouterProvider: string;
}

type Env = Record<string, string | undefined>;

export function loadConfig(env: Env = process.env): Config {
  const apiUrl = (env.API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
  return {
    apiUrl,
    model: env.MODEL || (env.OPENROUTER_API_KEY && !env.ANTHROPIC_API_KEY ? `openrouter:${env.OPENROUTER_CHAT_MODEL || "google/gemini-3.8-flash"}` : undefined),
    darwinMcpUrl: env.DARWIN_MCP_URL || "https://mcp.darwin.so/mcp",
    appOrigin: parseOrigin(env.APP_ORIGIN || "http://localhost:5173"),
    port: parsePort(env.PORT || "4000"),
    anthropicWorkspaceId: env.ANTHROPIC_WORKSPACE_ID || undefined,
    anthropicApiKey: env.ANTHROPIC_API_KEY || undefined,
    openrouterApiKey: env.OPENROUTER_API_KEY || undefined,
    openrouterProvider: env.OPENROUTER_PROVIDER || "google-vertex",
  };
}

function parseOrigin(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`APP_ORIGIN must be a URL origin, got "${value}"`);
  }
  if (!["http:", "https:"].includes(url.protocol) || url.origin === "null" ||
    url.pathname !== "/" ||
    url.search ||
    url.hash ||
    url.username ||
    url.password) {
    throw new Error(`APP_ORIGIN must be a bare origin (no path), got "${value}"`);
  }
  return url.origin;
}

function parsePort(value: string): number {
  const port = Number(value);
  if (!/^\d+$/.test(value) || port > 65535) {
    throw new Error(`PORT must be an integer 0-65535, got "${value}"`);
  }
  return port;
}

/** Select once from configuration; provider failures never trigger a different provider. */
export function chatConfigured(config: Config): boolean {
  if (!config.model) return false;
  if (/^anthropic[:/]/i.test(config.model)) return Boolean(config.anthropicApiKey);
  if (/^openrouter[:/]/i.test(config.model)) return Boolean(config.openrouterApiKey);
  return true;
}
