/** Isolated browser regression: `bun run test:browser` (requires Chrome on macOS). No model calls. */
import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { strict as assert } from 'node:assert';
import { match, result, resumeId } from './fixtures.ts';

const realEnvPath = resolve(import.meta.dir, '../.svelte-kit/generated/dev/env/config.js');
const readRealEnv = () => existsSync(realEnvPath) ? readFileSync(realEnvPath, 'utf8') : null;
const originalRealEnv = readRealEnv();
let apiAvailable = true;
const sample = result();
sample.resume.progress = { done: 130, total: 132, attempted: 132, succeeded: 130, failed: 2, pending: 0 };
sample.resume.status = 'partial';
sample.resume.effective_location = 'United States'; sample.resume.location_source = 'default';
sample.resume.search = { catalog_total: 10000, eligible_total: 9000, matching_postings: 2000,
	distinct_roles: 1000, shortlisted: 132, retrieval_limit: 1000, candidate_location_known: true, missing_descriptions: 0 };
const allMatches = Array.from({ length: 130 }, (_, index) => ({ ...match(index + 1), location: index >= 110 || index === 0 ? 0.8 : 0.1, source_status: index === 0 ? 'failed' : 'ok', source_verified_at: '2026-01-01T00:00:00Z', source_stale: index === 0 }));
const api = Bun.serve({ port: 0, hostname: '127.0.0.1', fetch(request) {
	if (!apiAvailable) return Response.json({ detail: 'Test unavailable' }, { status: 503 });
	const url = new URL(request.url);
	if (url.pathname.includes('match-results')) {
		const nearby = url.searchParams.get('nearby') === 'true';
		const limit = Number(url.searchParams.get('limit')); const offset = Number(url.searchParams.get('offset'));
		const filtered = nearby ? allMatches.filter((m) => m.location >= 0.5) : allMatches;
		return Response.json({ ...sample.matches, candidate_location_known: true, effective_location: 'United States', location_source: 'default',
			items: filtered.slice(offset, offset + limit), total: filtered.length, unfiltered_total: allMatches.length,
			nearby_total: 21, limit, offset, has_more: offset + limit < filtered.length });
	}
	return Response.json(sample.resume);
} });
// Separate .svelte-kit output prevents this fixture API from changing a running dev server's environment.
const isolatedFrontend = mkdtempSync(join(tmpdir(), 'speedhack-frontend-'));
for (const path of ['src', 'static', 'package.json', 'tsconfig.json', 'vite.config.ts', 'bunfig.toml']) {
	cpSync(resolve(import.meta.dir, '..', path), join(isolatedFrontend, path), { recursive: true });
}
symlinkSync(realpathSync(resolve(import.meta.dir, '../node_modules')), join(isolatedFrontend, 'node_modules'), 'dir');
const frontendPort = 18000 + Math.floor(Math.random() * 1000);
const frontend = spawn('bun', ['run', 'dev', '--host', '127.0.0.1', '--port', String(frontendPort), '--strictPort'], {
	cwd: isolatedFrontend, env: { ...process.env, API_URL: `http://127.0.0.1:${api.port}/` }, stdio: 'pipe'
});
let logs = ''; frontend.stdout?.on('data', (data) => logs += data); frontend.stderr?.on('data', (data) => logs += data);
const chromePort = 19000 + Math.floor(Math.random() * 1000);
const profile = mkdtempSync(join(tmpdir(), 'speedhack-browser-'));
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
	'--headless=new', `--remote-debugging-port=${chromePort}`, `--user-data-dir=${profile}`, '--no-first-run', '--hide-scrollbars', 'about:blank'
], { stdio: 'ignore' });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let ws: WebSocket | undefined;
try {
	const base = `http://127.0.0.1:${frontendPort}`;
	let targets: any[] = [];
	for (let attempt = 0; attempt < 100; attempt++) {
		try {
			const response = await fetch(base); targets = await (await fetch(`http://127.0.0.1:${chromePort}/json/list`)).json();
			if (response.ok && targets.some((t) => t.type === 'page')) break;
		} catch {}
		await sleep(100);
	}
	ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
	await new Promise((r) => ws!.addEventListener('open', r, { once: true }));
	let sequence = 0;
	const pending = new Map<number, { resolve: (value: any) => void; reject: (value: any) => void }>();
	const exceptions: string[] = [];
	ws.addEventListener('message', (event) => {
		const message = JSON.parse(String(event.data));
		if (message.id) { const item = pending.get(message.id); if (message.error) item?.reject(message.error); else item?.resolve(message.result); pending.delete(message.id); }
		if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails.exception?.description ?? 'Unknown browser exception');
	});
	const send = (method: string, params = {}): Promise<any> => new Promise((resolve, reject) => {
		pending.set(++sequence, { resolve, reject }); ws!.send(JSON.stringify({ id: sequence, method, params }));
	});
	const evaluate = async (expression: string) => {
		const response = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
		if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description ?? 'Browser evaluation failed');
		return response.result.value;
	};
	const waitFor = async (expression: string) => {
		for (let attempt = 0; attempt < 100; attempt++) { if (await evaluate(expression)) return; await sleep(100); }
		throw new Error(`Timed out: ${expression}\n${await evaluate('document.body.innerText')}`);
	};
	await send('Page.enable'); await send('Runtime.enable');
	await send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 1000, deviceScaleFactor: 1, mobile: false });
	await send('Page.navigate', { url: `${base}/resumes/${resumeId}` });
	await waitFor(`document.querySelectorAll('.matches > li').length === 100`);
	assert.equal(await evaluate(`document.querySelector('.filter input').checked`), false);
	assert.equal(await evaluate(`document.body.innerText.includes('Using United States')`), true);
	assert.equal(await evaluate(`document.body.innerText.includes('Source not recently verified')`), true);
	assert.equal(await evaluate(`document.body.innerText.includes('2 could not be scored')`), true);
	await evaluate(`[...document.querySelectorAll('button')].find(b => b.textContent.includes('Load 100')).click()`);
	await waitFor(`document.querySelectorAll('.matches > li').length === 130`);
	await evaluate(`document.querySelector('.filter input').click()`);
	await waitFor(`document.querySelectorAll('.matches > li').length === 21`);
	assert.equal(await evaluate(`document.body.innerText.includes('Engineer 130')`), true);
	await send('Page.reload');
	await waitFor(`document.querySelector('.filter input')?.checked && document.querySelectorAll('.matches > li').length === 21`);
	const cache = await evaluate(`localStorage.getItem('speedhack.resume-history.v1')`);
	assert.equal(cache.includes('Private'), false); assert.equal(cache.includes('private@example'), false);
	const shot = await send('Page.captureScreenshot', { format: 'png' });
	await Bun.write(resolve(import.meta.dir, '../../.context/frontend-fixed-results.png'), Buffer.from(shot.data, 'base64'));
	await send('Page.navigate', { url: base });
	await waitFor(`document.querySelector('.history a') !== null`);
	await evaluate(`document.querySelector('.history a').click()`);
	await waitFor(`document.querySelectorAll('.matches > li').length === 21`);
	apiAvailable = false;
	await send('Page.reload');
	await waitFor(`document.body.innerText.includes('Saved browser preview') && document.querySelectorAll('.matches > li').length === 21`);
	assert.equal(await evaluate(`document.body.innerText.includes('Source not recently verified')`), true);
	apiAvailable = true;
	await evaluate(`[...document.querySelectorAll('button')].find(b => b.textContent === 'Reconnect').click()`);
	await waitFor(`document.querySelector('.filter input')?.checked && document.querySelectorAll('.matches > li').length === 21`);
	await send('Page.navigate', { url: base });
	await waitFor(`document.querySelector('.history a') !== null`);
	await evaluate(`[...document.querySelectorAll('button')].find(b => b.textContent === 'Clear browser history').click()`);
	await waitFor(`localStorage.getItem('speedhack.resume-history.v1') === null && !document.querySelector('.history')`);
	assert.deepEqual(exceptions, []);
	assert.equal(readRealEnv(), originalRealEnv, 'The browser test must not modify the running dev server environment');
	console.log('PASS: all-location default, US fallback, partial counts, >100 load-more, server filtering, filter persistence across reload, homepage reopen, offline cache, reconnect, cache privacy, history clearing, no browser exceptions, real dev environment unchanged.');
} catch (error) { console.error(logs); throw error; }
finally {
	ws?.close(); chrome.kill(); frontend.kill(); api.stop(true);
	await sleep(200); rmSync(profile, { recursive: true, force: true }); rmSync(isolatedFrontend, { recursive: true, force: true });
}
