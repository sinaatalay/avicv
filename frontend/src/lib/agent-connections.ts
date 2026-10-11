import * as v from 'valibot';

export const clients = [
	{ id: 'codex', name: 'Codex' }, { id: 'claude', name: 'Claude Code' },
	{ id: 'cursor', name: 'Cursor' }, { id: 'vscode', name: 'VS Code' }
] as const;
export type AgentClient = typeof clients[number]['id'];
export const connectionSchema = v.object({
	id: v.pipe(v.string(), v.regex(/^[a-f0-9]{32}$/)), name: v.string(), created_at: v.string(),
	last_used_at: v.nullable(v.string()), revoked_at: v.nullable(v.string())
});
export const connectionListSchema = v.object({ items: v.array(connectionSchema) });
export const issuedConnectionSchema = v.object({ ...connectionSchema.entries, token: v.pipe(v.string(), v.minLength(1)) });
export type Connection = v.InferOutput<typeof connectionSchema>;

/** Configuration contains a secret reference, never the user's actual credential. */
export function agentSetup(client: AgentClient, origin: string) {
	const url = new URL('/mcp', origin).href;
	if (client === 'codex') return {
		label: 'Run in the terminal where you start Codex',
		text: `codex mcp add avicv --url ${url} --bearer-token-env-var AVICV_TOKEN`,
		note: 'Set AVICV_TOKEN in that terminal before starting Codex.'
	};
	if (client === 'claude') return {
		label: 'Run in the terminal where you start Claude Code',
		text: `claude mcp add --transport http --scope user avicv ${url} --header 'Authorization: Bearer \${AVICV_TOKEN}'`,
		note: 'Set AVICV_TOKEN in that terminal before starting Claude Code.'
	};
	if (client === 'cursor') return {
		label: 'Add to ~/.cursor/mcp.json',
		text: JSON.stringify({ mcpServers: { avicv: { url, headers: { Authorization: 'Bearer ${env:AVICV_TOKEN}' } } } }, null, 2),
		note: 'Set AVICV_TOKEN in the environment where Cursor starts, then restart Cursor.'
	};
	return {
		label: 'In VS Code, run “MCP: Open User Configuration” and merge this setup',
		text: JSON.stringify({ inputs: [{ type: 'promptString', id: 'avicv-token', description: 'AviCV connection token', password: true }],
			servers: { avicv: { type: 'http', url, headers: { Authorization: 'Bearer ${input:avicv-token}' } } } }, null, 2),
		note: 'For classic VS Code MCP. Start the server and paste your token when prompted.'
	};
}
