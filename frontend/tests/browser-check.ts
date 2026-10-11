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
let allMatches = Array.from({ length: 130 }, (_, index) => ({
	...match(index + 1), score: index < 115 ? 0.9 - index * 0.0008 : 0.8 - (index - 115) * 0.01,
	url: index === 129 ? null : `https://example.com/jobs/${index + 1}`,
	location: 0.8, source_status: 'ok', source_verified_at: '2026-01-01T00:00:00Z', source_stale: false
}));
const api = Bun.serve({ port: 0, hostname: '127.0.0.1', fetch(request) {
	if (!apiAvailable) return Response.json({ detail: 'Test unavailable' }, { status: 503 });
	const url = new URL(request.url);
	if (url.pathname.includes('match-results')) {
		const category = url.searchParams.get('category') ?? 'all';
		const limit = Number(url.searchParams.get('limit')); const offset = Number(url.searchParams.get('offset'));
		const good = allMatches.filter((m) => m.score > 0.8);
		const other = allMatches.filter((m) => m.score <= 0.8);
		const filtered = category === 'good' ? good : category === 'other' ? other : allMatches;
		return Response.json({ ...sample.matches, candidate_location_known: true, effective_location: 'United States', location_source: 'default',
			items: filtered.slice(offset, offset + limit), total: filtered.length, unfiltered_total: allMatches.length,
			good_total: good.length, other_total: other.length, nearby_total: allMatches.length, limit, offset, has_more: offset + limit < filtered.length });
	}
	return Response.json({ ...sample.resume, id: url.pathname.split('/')[2] });
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
	const changePage = async (method: string, params = {}) => {
		let listener: (event: MessageEvent) => void;
		let timer: ReturnType<typeof setTimeout>;
		const loaded = new Promise<void>((resolve, reject) => {
			timer = setTimeout(() => reject(new Error('Navigation did not finish')), 15000);
			listener = (event) => { if (JSON.parse(String(event.data)).method === 'Page.loadEventFired') resolve(); };
			ws!.addEventListener('message', listener);
		});
		try { await send(method, params); await loaded; }
		finally { clearTimeout(timer!); ws!.removeEventListener('message', listener!); }
	};
	await send('Page.enable'); await send('Runtime.enable');
	await send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 1000, deviceScaleFactor: 1, mobile: false });
	await changePage('Page.navigate', { url: `${base}/resumes/${resumeId}` });
	const tab = (name: string) => `[...document.querySelectorAll('[role=tab]')].find(b => b.textContent.includes('${name}'))`;
	const selected = (name: string) => `${tab(name)}?.getAttribute('aria-selected') === 'true'`;
	const clickTab = async (name: string) => evaluate(`${tab(name)}.click()`);
	await waitFor(`document.querySelectorAll('.matches > li').length === 20 && ${selected('Good matches')}`);
	assert.equal(await evaluate(`${tab('Good matches')}.textContent.includes('115')`), true);
	assert.equal(await evaluate(`${tab('Other jobs')}.textContent.includes('15')`), true);
	assert.equal(await evaluate(`document.body.innerText.includes('Engineer 1')`), true);
	const body = await evaluate('document.body.innerText');
	assert.equal(/\d+%/.test(body), false, 'No match percentages');
	for (const term of ['Open postings', 'Keyword', 'shortlist', 'Estimated run cost', 'Model assessment', 'Source not recently verified', 'RenderCV']) {
		assert.equal(body.includes(term), false, `No engineering copy: ${term}`);
	}
	const firstApply = await evaluate(`[...document.querySelectorAll('.matches a')].find(a => a.textContent.trim().startsWith('Apply'))?.href`);
	assert.equal(firstApply, 'https://example.com/jobs/1');
	assert.equal(await evaluate(`[...document.querySelectorAll('.matches a')].find(a => a.textContent.trim().startsWith('Apply'))?.rel.includes('noopener')`), true);
	await evaluate(`document.getElementById('good-tab').focus()`);
	for (const [key, code, categoryName, count] of [['ArrowRight', 39, 'Other jobs', 15], ['ArrowLeft', 37, 'Good matches', 20]] as const) {
		await send('Input.dispatchKeyEvent', { type: 'keyDown', key, code: key, windowsVirtualKeyCode: code });
		await send('Input.dispatchKeyEvent', { type: 'keyUp', key, code: key, windowsVirtualKeyCode: code });
		await waitFor(`document.querySelectorAll('.matches > li').length === ${count} && ${selected(categoryName)}`);
		assert.equal(await evaluate(`document.activeElement?.getAttribute('aria-selected')`), 'true');
	}
	for (const expected of [40, 60, 80, 100, 115]) {
		await evaluate(`[...document.querySelectorAll('button')].find(b => /Show more|Load more|Load 20|More jobs/i.test(b.textContent)).click()`);
		await waitFor(`document.querySelectorAll('.matches > li').length === ${expected}`);
	}
	await clickTab('Other jobs');
	await waitFor(`document.querySelectorAll('.matches > li').length === 15 && ${selected('Other jobs')}`);
	assert.equal(await evaluate(`document.querySelector('.matches > li')?.textContent.includes('Engineer 116')`), true, 'Exact .8 belongs to Other');
	assert.equal(await evaluate(`document.querySelector('.matches > li:last-child')?.querySelector('a') === null`), true, 'Never fabricate missing application links');
	await changePage('Page.reload');
	await waitFor(`document.querySelectorAll('.matches > li').length === 15 && ${selected('Other jobs')}`);
	await clickTab('Good matches');
	await waitFor(`document.querySelectorAll('.matches > li').length === 115 && ${selected('Good matches')}`);
	await clickTab('Other jobs');
	await waitFor(`document.querySelectorAll('.matches > li').length === 15 && ${selected('Other jobs')}`);
	const cache = await evaluate(`localStorage.getItem('speedhack.resume-history.v1')`);
	assert.equal(cache.includes('Private'), false); assert.equal(cache.includes('private@example'), false);
	await changePage('Page.navigate', { url: base });
	await waitFor(`document.querySelector('.history a') !== null`);
	await evaluate(`document.querySelector('.history a').click()`);
	await waitFor(`document.querySelectorAll('.matches > li').length === 15 && ${selected('Other jobs')}`);
	apiAvailable = false;
	await changePage('Page.reload');
	await waitFor(`document.querySelectorAll('.matches > li').length === 15 && document.body.innerText.toLowerCase().includes('saved')`);
	assert.equal(await evaluate(`document.querySelector('.matches > li')?.textContent.includes('Engineer 116')`), true);
	apiAvailable = true;
	await evaluate(`[...document.querySelectorAll('button')].find(b => /Reconnect|Try again/.test(b.textContent)).click()`);
	await waitFor(`document.querySelectorAll('.matches > li').length === 15 && ${selected('Other jobs')}`);
	await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
	assert.equal(await evaluate(`document.documentElement.scrollWidth <= window.innerWidth`), true, 'Mobile has no horizontal overflow');
	const shot = await send('Page.captureScreenshot', { format: 'png' });
	await Bun.write(resolve(import.meta.dir, '../../.context/simple-results-mobile-fixture.png'), Buffer.from(shot.data, 'base64'));
	await changePage('Page.navigate', { url: base });
	await waitFor(`document.querySelector('.history a') !== null`);
	await evaluate(`[...document.querySelectorAll('button')].find(b => b.textContent === 'Clear list').click()`);
	await waitFor(`localStorage.getItem('speedhack.resume-history.v1') === null && !document.querySelector('.history')`);
	allMatches = allMatches.map((item, index) => ({ ...item, score: 0.8 - index * 0.001 }));
	sample.resume.status = 'done';
	await changePage('Page.navigate', { url: `${base}/resumes/${'b'.repeat(32)}` });
	await waitFor(`document.querySelectorAll('.matches > li').length === 20 && ${selected('Other jobs')}`);
	assert.equal(await evaluate(`document.body.innerText.includes('No good matches yet')`), true);
	assert.equal(await evaluate(`${tab('Good matches')}.textContent.includes('0')`), true);
	assert.deepEqual(exceptions, []);
	assert.equal(readRealEnv(), originalRealEnv, 'The browser test must not modify the running dev server environment');
	console.log('PASS: category boundary and full counts, independent pagination, direct Apply links, no percentages or diagnostics, reload and per-tab preferences, history, scoped offline cache, reconnect, mobile layout, zero-good fallback, browser isolation.');

} catch (error) { console.error(logs); throw error; }
finally {
	ws?.close(); chrome.kill(); frontend.kill(); api.stop(true);
	await sleep(200); rmSync(profile, { recursive: true, force: true }); rmSync(isolatedFrontend, { recursive: true, force: true });
}
