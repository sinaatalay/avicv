/** Isolated browser regression: `bun run test:browser` (requires Chrome on macOS). No model calls. */
import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { strict as assert } from 'node:assert';
import { match, result, resumeId } from './fixtures.ts';

const realEnvPath = resolve(import.meta.dir, '../.svelte-kit/generated/dev/env/config.js');
const readRealEnv = () => existsSync(realEnvPath) ? readFileSync(realEnvPath, 'utf8') : null;
const originalRealEnv = readRealEnv();
let apiAvailable = true;
let tailoringReady = false;
let tailorPosts = 0;
let pdfDownloads = 0;
const artifactId = 'c'.repeat(32);
const pdfFixture = (() => {
	const content = 'BT /F1 18 Tf 30 100 Td (Tailored CV fixture) Tj ET';
	const objects = [
		'<< /Type /Catalog /Pages 2 0 R >>',
		'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
		'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
		'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
		`<< /Length ${content.length} >>\nstream\n${content}\nendstream`
	];
	let pdf = '%PDF-1.4\n'; const offsets = [0];
	objects.forEach((object, index) => { offsets.push(pdf.length); pdf += `${index + 1} 0 obj\n${object}\nendobj\n`; });
	const xref = pdf.length;
	pdf += `xref\n0 ${offsets.length}\n0000000000 65535 f \n${offsets.slice(1).map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')}`;
	return `${pdf}trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
})();
const sample = result();
sample.resume.progress = { done: 130, total: 132, attempted: 132, succeeded: 130, failed: 2, pending: 0 };
sample.resume.status = 'partial';
sample.resume.effective_location = 'United States'; sample.resume.location_source = 'default';
sample.resume.search = { catalog_total: 10000, eligible_total: 9000, matching_postings: 2000,
	distinct_roles: 1000, shortlisted: 132, retrieval_limit: 1000, candidate_location_known: true, missing_descriptions: 0 };
let allMatches = Array.from({ length: 130 }, (_, index) => ({
	...match(index + 1), score: index < 115 ? 0.9 - index * 0.0008 : 0.8 - (index - 115) * 0.01,
	title: index === 115 ? 'Engineer 116 — Member of Technical Staff (Software Engineer, Computer)' : `Engineer ${index + 1}`,
	url: index === 129 ? null : `https://example.com/jobs/${index + 1}`,
	location: 0.8, source_status: 'ok', source_verified_at: '2026-01-01T00:00:00Z', source_stale: false
}));
const api = Bun.serve({ port: 0, hostname: '127.0.0.1', async fetch(request) {
	if (!apiAvailable) return Response.json({ detail: 'Test unavailable' }, { status: 503 });
	const url = new URL(request.url);
	if (url.pathname.endsWith('/tailor') && request.method === 'POST') {
		const { job_key } = await request.json();
		if (job_key === '2') return Response.json({ detail: 'This job’s description is unavailable. Choose another job.' }, { status: 422 });
		assert.equal(job_key, '1');
		tailorPosts++;
		return Response.json({ id: artifactId, status: tailoringReady ? 'ready' : 'waiting_for_design', error: null,
			design_source: 'reconstructed', download_url: null }, { status: 202 });
	}
	if (url.pathname.endsWith('/cv.pdf')) {
		pdfDownloads++;
		return new Response(pdfFixture, { headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="Candidate-Engineer.pdf"' } });
	}
	if (url.pathname.includes('/tailored/')) {
		return Response.json({ id: artifactId, status: tailoringReady ? 'ready' : 'waiting_for_design', error: null,
			design_source: 'reconstructed', download_url: 'https://untrusted.example/cv.pdf' });
	}
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
const downloads = mkdtempSync(join(tmpdir(), 'speedhack-downloads-'));
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
	const assertActionRail = async (besideDetails = true) => {
		const layout = await evaluate(`(() => {
			const card = document.querySelector('.matches > li');
			const actions = [...card.querySelector('.job-actions').children];
			const rectangles = actions.map((action) => action.getBoundingClientRect());
			const details = card.querySelector('.job-details').getBoundingClientRect();
			return { vertical: rectangles.every((rect, index) => index === 0 || (Math.abs(rect.left - rectangles[0].left) < 1 && Math.abs(rect.width - rectangles[0].width) < 1 && rect.top >= rectangles[index - 1].bottom)),
				right: rectangles[0].left >= details.right,
				iconsLeft: actions.every((action) => action.firstElementChild?.tagName.toLowerCase() === 'svg' && action.querySelector('svg').getBoundingClientRect().right <= action.querySelector('span').getBoundingClientRect().left) };
		})()`);
		assert.equal(layout.vertical, true, 'Actions form an equal-width vertical rail');
		assert.equal(layout.iconsLeft, true, 'Every button has its Lucide icon on the left');
		if (besideDetails) assert.equal(layout.right, true, 'Action rail stays to the right of job details');
	};
	await send('Page.enable'); await send('Runtime.enable');
	await send('Emulation.setFocusEmulationEnabled', { enabled: true });
	await send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads });
	await send('Browser.grantPermissions', { origin: base, permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'] });
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
	assert.equal(await evaluate(`document.querySelector('.job-actions').children.length`), 3, 'Exactly three job actions');
	await assertActionRail();
	const firstCard = `document.querySelector('.matches > li')`;
	const copyPosition = await evaluate(`(() => { const r = ${firstCard}.querySelector('.job-actions button').getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
	await send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...copyPosition });
	await send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...copyPosition });
	await waitFor(`${firstCard}.innerText.includes('Copied')`);
	assert.equal(await evaluate(`navigator.clipboard.readText()`), 'https://example.com/jobs/1');
	await evaluate(`Object.defineProperty(navigator.clipboard, 'writeText', { configurable: true, value: async () => { throw new Error('Denied'); } }); document.querySelectorAll('.matches > li')[1].querySelector('.job-actions button').click()`);
	await waitFor(`document.querySelector('.copy-fallback input') !== null`);
	assert.equal(await evaluate(`document.querySelector('.copy-fallback input').value`), 'https://example.com/jobs/2');
	await evaluate(`document.querySelector('.copy-fallback input').focus()`);
	assert.equal(await evaluate(`document.querySelector('.copy-fallback input').selectionEnd`), 'https://example.com/jobs/2'.length);
	await evaluate(`document.querySelectorAll('.matches > li')[1].querySelector('.job-actions button:last-child').click()`);
	await waitFor(`document.querySelectorAll('.matches > li')[1].innerText.includes('Choose another job')`);
	await evaluate(`${firstCard}.querySelector('.job-actions button:last-child').click()`);
	await waitFor(`${firstCard}.innerText.includes('Preparing CV…') && localStorage.getItem('speedhack.tailored-cvs.v1')?.includes('${artifactId}')`);
	assert.equal(tailorPosts, 1);
	await clickTab('Other jobs');
	await waitFor(selected('Other jobs'));
	await changePage('Page.reload');
	await waitFor(`document.querySelectorAll('.matches > li').length === 15 && ${selected('Other jobs')}`);
	assert.equal(tailorPosts, 1, 'Refresh never creates another tailored CV');
	tailoringReady = true;
	await waitFor(`JSON.parse(localStorage.getItem('speedhack.tailored-cvs.v1')).entries[0].downloadPending === false`);
	for (let attempt = 0; attempt < 100 && !existsSync(join(downloads, 'Candidate-Engineer.pdf')); attempt++) await sleep(100);
	assert.equal(readFileSync(join(downloads, 'Candidate-Engineer.pdf'), 'utf8'), pdfFixture, 'Tailor action automatically saves an actual PDF');
	assert.equal(pdfDownloads, 1);
	await clickTab('Good matches');
	await waitFor(`${selected('Good matches')} && ${firstCard}.innerText.includes('Download CV')`);
	await evaluate(`${firstCard}.querySelector('.job-actions button:last-child').click()`);
	await waitFor(`${firstCard}.querySelector('.job-actions button:last-child').disabled === false`);
	assert.equal(tailorPosts, 2, 'Explicit re-download revalidates the cached version');
	assert.equal(pdfDownloads, 2);
	assert.equal(readdirSync(downloads).some((name) => name.endsWith('.pdf')), true);
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
	assert.equal(pdfDownloads, 2, 'Completed downloads do not repeat on refresh');
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
	const desktopShot = await send('Page.captureScreenshot', { format: 'png' });
	await Bun.write(resolve(import.meta.dir, '../../.context/job-actions-desktop-fixture.png'), Buffer.from(desktopShot.data, 'base64'));
	await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
	assert.equal(await evaluate(`document.documentElement.scrollWidth <= window.innerWidth`), true, 'Mobile has no horizontal overflow');
	await assertActionRail();
	const shot = await send('Page.captureScreenshot', { format: 'png' });
	await Bun.write(resolve(import.meta.dir, '../../.context/job-actions-mobile-fixture.png'), Buffer.from(shot.data, 'base64'));
	await send('Emulation.setDeviceMetricsOverride', { width: 320, height: 844, deviceScaleFactor: 1, mobile: true });
	assert.equal(await evaluate(`document.documentElement.scrollWidth <= window.innerWidth`), true, 'Narrow phones have no horizontal overflow');
	await assertActionRail(false);
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
	console.log('PASS: three actions, direct Apply links, clipboard success and denied fallback, durable tailoring and automatic PDF download after reload/tab switch, version revalidation, category boundary and counts, independent pagination, no percentages or diagnostics, history and preferences, offline/reconnect, mobile layout, zero-good fallback, browser isolation.');

} catch (error) { console.error(logs); throw error; }
finally {
	ws?.close(); chrome.kill(); frontend.kill(); api.stop(true);
	await sleep(200); rmSync(profile, { recursive: true, force: true }); rmSync(isolatedFrontend, { recursive: true, force: true }); rmSync(downloads, { recursive: true, force: true });
}
