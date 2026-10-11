/** Production process supervisor: one public website, private API/chat, durable jobs. */
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dir, '..');
const port = process.env.PORT || '8080';
const apiPort = process.env.API_PORT || '8000';
const agentPort = process.env.AGENT_PORT || '4000';
const origin = process.env.APP_ORIGIN || `http://localhost:${port}`;
const common = {
	...process.env,
	APP_ORIGIN: origin,
	API_URL: `http://127.0.0.1:${apiPort}`,
	AGENT_URL: `http://127.0.0.1:${agentPort}`,
};
const python = process.env.PYTHON || resolve(root, '.venv/bin/python');
if (!existsSync(resolve(root, 'frontend/build/index.js')) || !existsSync(resolve(root, 'agent/dist/chat/index.html'))) {
	throw new Error('Run bun run build before bun run start.');
}
if (!existsSync(python)) throw new Error('Run uv sync before bun run start.');

const children: { name: string; process: ReturnType<typeof Bun.spawn> }[] = [];
let stopping = false;
function start(name: string, cmd: string[], cwd: string, env: Record<string, string | undefined> = {}) {
	const child = Bun.spawn(cmd, { cwd: resolve(root, cwd), env: { ...common, ...env }, stdout: 'inherit', stderr: 'inherit', stdin: 'ignore' });
	children.push({ name, process: child });
	void child.exited.then((code) => {
		if (!stopping) {
			console.error(`${name} exited (${code}); stopping AviCV so the host can restart it.`);
			void stop(code || 1);
		}
	});
}
async function stop(code: number) {
	if (stopping) return;
	stopping = true;
	for (const child of children) child.process.kill('SIGTERM');
	const timeout = setTimeout(() => { for (const child of children) child.process.kill('SIGKILL'); }, 8000);
	await Promise.allSettled(children.map((child) => child.process.exited));
	clearTimeout(timeout);
	process.exit(code);
}
for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) process.on(signal, () => void stop(0));

start('API/MCP', [python, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', apiPort, '--no-access-log'], 'backend');
const envFiles = ['backend/.env', 'agent/.env'].filter((file) => existsSync(resolve(root, file))).flatMap((file) => ['--env-file', resolve(root, file)]);
start('CopilotKit/Darwin', [process.execPath, ...envFiles, 'src/server.ts'], 'agent', { PORT: agentPort });
if (process.env.JOBS_SYNC_ENABLED !== 'false') start('Job catalog', [python, '-m', 'backend.jobs.ingest', '--loop'], 'backend');
// The website proxies /mcp, uploads and chat as streams. No other port is public.
start('Website', [process.execPath, './build'], 'frontend', {
	PORT: port, HOST: process.env.HOST || '0.0.0.0',
	BODY_SIZE_LIMIT: '12M', CONNECTION_IDLE_TIMEOUT: '120'
});
console.log(`AviCV: ${origin} · MCP: ${origin}/mcp`);
