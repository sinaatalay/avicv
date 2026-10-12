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
let bulkLinksMode: 'normal' | 'empty' | 'unavailable' = 'normal';
let bulkLinkRequests = 0;
let savingsMode: 'normal' | 'zero' | 'unavailable' = 'normal';
let savingsRequests = 0;
let tailoringReady = false;
let tailorPosts = 0;
let pdfDownloads = 0;
let pdfUnavailable = false;
const artifactId = 'c'.repeat(32);
const sessionCookie = 'fixture-session-opaque-value';
const workspaceId = '1'.repeat(32);
const cacheDigest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(sessionCookie));
const cacheId = [...new Uint8Array(cacheDigest)].slice(0, 16).map((byte) => byte.toString(16).padStart(2, '0')).join('');
const historyKey = `avicv.workspace.${cacheId}.speedhack.resume-history.v1`;
const tailorKey = `avicv.workspace.${cacheId}.speedhack.tailored-cvs.v1`;
let agentUploaded = false;
const agentResumeId = 'e'.repeat(32);
const connectionId = 'f'.repeat(32);
let connection: { id: string; name: string; created_at: string; last_used_at: string | null; revoked_at: string | null } | null = null;
const tokenSecret = 'fixture-secret-never-in-localstorage';
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
	if (url.pathname === '/auth/session') return Response.json({ workspace_id: workspaceId }, {
		headers: { 'set-cookie': `avicv_session=${sessionCookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000` }
	});
	assert.equal(request.headers.get('cookie'), `avicv_session=${sessionCookie}`, 'Only AviCV session reaches the private API');
	assert.equal(request.headers.has('authorization'), false, 'Website API never forwards caller bearer credentials');
	if (url.pathname === '/auth/tokens') {
		if (request.method === 'POST') {
			const body = await request.json();
			connection = { id: connectionId, name: body.name, created_at: '2026-01-01T00:00:00Z', last_used_at: null, revoked_at: null };
			return Response.json({ ...connection, token: tokenSecret }, { status: 201 });
		}
		return Response.json({ items: connection ? [connection] : [] });
	}
	if (url.pathname === `/auth/tokens/${connectionId}` && request.method === 'DELETE') { connection = null; return new Response(null, { status: 204 }); }
	if (url.pathname === '/resumes') {
		const items = [{ id: resumeId, filename: sample.resume.filename, status: sample.resume.status, created_at: sample.resume.created_at,
			good_total: allMatches.filter((item) => item.score > 0.8).length }];
		if (agentUploaded) items.unshift({ ...items[0], id: agentResumeId, filename: 'Agent-uploaded CV.pdf', created_at: '2026-10-12T00:00:00Z' });
		return Response.json({ items, total: items.length, offset: 0, limit: 100, has_more: false });
	}
	if (url.pathname.endsWith('/tailored')) return Response.json({ items: tailorPosts ? [{ id: artifactId,
		status: tailoringReady ? 'ready' : 'waiting_for_design', error: null, design_source: 'reconstructed', download_url: null,
		job_key: '1', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }] : [],
		total: tailorPosts ? 1 : 0, offset: 0, limit: 100, has_more: false });
	if (url.pathname.endsWith('/time-savings')) {
		savingsRequests++;
		if (savingsMode === 'unavailable') return Response.json({ detail: 'Savings unavailable' }, { status: 503 });
		const jobsReviewed = savingsMode === 'zero' ? 0 : 130;
		const cvsTailored = savingsMode === 'zero' || !tailoringReady ? 0 : 1;
		return Response.json({ jobs_reviewed: jobsReviewed, cvs_tailored: cvsTailored,
			job_review_seconds: 30, cv_tailoring_seconds: 600,
			estimated_seconds: jobsReviewed * 30 + cvsTailored * 600 });
	}
	if (url.pathname.endsWith('/good-match-links')) {
		bulkLinkRequests++;
		if (bulkLinksMode === 'unavailable') return Response.json({ detail: 'Links unavailable' }, { status: 503 });
		const links = bulkLinksMode === 'empty' ? [] : [...new Set(allMatches
			.filter((item) => item.score > 0.8 && item.catalog_status !== 'closed' && item.url && /^https?:\/\//.test(item.url))
			.map((item) => item.url!))];
		return Response.json({ links });
	}
	if (url.pathname.endsWith('/tailor') && request.method === 'POST') {
		const { job_key } = await request.json();
		if (job_key === '2') return Response.json({ detail: 'This job’s description is unavailable. Choose another job.' }, { status: 422 });
		assert.equal(job_key, '1');
		tailorPosts++;
		return Response.json({ id: artifactId, status: tailoringReady ? 'ready' : 'waiting_for_design', error: null,
			design_source: 'reconstructed', download_url: null }, { status: 202 });
	}
	if (url.pathname.endsWith('/cv.pdf')) {
		if (pdfUnavailable) return Response.json({ detail: 'Download temporarily unavailable' }, { status: 503 });
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
	cwd: isolatedFrontend, env: { ...process.env, API_URL: `http://127.0.0.1:${api.port}/`, APP_ORIGIN: `http://127.0.0.1:${frontendPort}` }, stdio: 'pipe'
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
	const savings = `document.querySelector('.time-savings')`;
	const savingsValue = `document.querySelector('.time-savings-value')?.textContent.trim()`;
	const assertSavings = async (value: string, detail: string) => {
		await waitFor(`${savingsValue} === ${JSON.stringify(value)}`);
		const savingsText = await evaluate(`${savings}.textContent.replace(/\\s+/g, ' ').trim()`);
		assert.ok(savingsText.includes(detail), `Expected savings detail ${JSON.stringify(detail)} in ${JSON.stringify(savingsText)}`);
		assert.equal(await evaluate(`${savings}.textContent.includes('Estimated time saved')`), true, 'Always label time saved as an estimate');
	};
	await assertSavings('~1h 5m saved', '130 jobs checked');
	assert.equal(await evaluate(`${savings}.textContent.includes('132 jobs')`), false, 'Unsuccessful assessments do not inflate savings');
	assert.equal(await evaluate(`${savings}.querySelector('details').open`), false, 'Calculation stays behind a compact disclosure');
	await evaluate(`${savings}.querySelector('summary').click()`);
	assert.equal(await evaluate(`${savings}.innerText.includes('30 sec per job checked + 10 min per tailored CV.')`), true);
	assert.equal(await evaluate(`${savings}.innerText.includes('actual times vary')`), true);
	assert.equal(await evaluate(`${savings}.innerText.includes('Downloads and retries add no time.')`), true);
	await evaluate(`${savings}.querySelector('summary').click()`);
	assert.equal(await evaluate(`${tab('Good matches')}.textContent.includes('115')`), true);
	assert.equal(await evaluate(`${tab('Other jobs')}.textContent.includes('15')`), true);
	assert.equal(await evaluate(`document.body.innerText.includes('Engineer 1')`), true);
	const body = await evaluate('document.body.innerText');
	assert.equal(/\d+%/.test(body), false, 'No match percentages');
	for (const term of ['Open postings', 'Keyword', 'shortlist', 'Estimated run cost', 'Model assessment', 'Source not recently verified', 'RenderCV']) {
		assert.equal(body.includes(term), false, `No engineering copy: ${term}`);
	}
	const bulkCopy = `document.querySelector('.copy-all-button')`;
	const clickBulkCopy = async () => {
		const position = await evaluate(`(() => { const r = ${bulkCopy}.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
		await send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...position });
		await send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...position });
	};
	const expectedGoodLinks = Array.from({ length: 115 }, (_, index) => `https://example.com/jobs/${index + 1}`).join('\n');
	assert.equal(await evaluate(`${bulkCopy}?.textContent.trim()`), 'Copy all links');
	assert.equal(await evaluate(`${bulkCopy}.firstElementChild.tagName.toLowerCase()`), 'svg', 'Bulk copy has a Lucide icon');
	await clickBulkCopy();
	await waitFor(`${bulkCopy}.textContent.trim() === 'Copied 115 links'`);
	assert.equal(await evaluate(`navigator.clipboard.readText()`), expectedGoodLinks, 'Copies all 115 good links in rank order, including 95 jobs not yet rendered');
	assert.equal(await evaluate(`document.querySelectorAll('.matches > li').length`), 20, 'Bulk copy does not expand or rely on visible cards');
	assert.equal(bulkLinkRequests, 1);
	bulkLinksMode = 'unavailable';
	await clickBulkCopy();
	await waitFor(`[...document.querySelectorAll('[role=status]')].some(node => /couldn.t load your links/i.test(node.textContent))`);
	assert.equal(await evaluate(`navigator.clipboard.readText()`), expectedGoodLinks, 'An endpoint failure never copies a partial visible list');
	bulkLinksMode = 'empty';
	await clickBulkCopy();
	await waitFor(`/no application links|no links available/i.test(document.body.innerText)`);
	assert.equal(await evaluate(`navigator.clipboard.readText()`), expectedGoodLinks, 'An empty list preserves the clipboard');
	bulkLinksMode = 'normal';
	await evaluate(`for (const method of ['write', 'writeText']) Object.defineProperty(navigator.clipboard, method, { configurable: true, value: async () => { throw new Error('Denied'); } }); ${bulkCopy}.click()`);
	await waitFor(`document.querySelector('#job-results textarea') !== null`);
	assert.equal(await evaluate(`document.querySelector('#job-results textarea').value`), expectedGoodLinks, 'Clipboard denial exposes all links for manual copying');
	await evaluate(`document.querySelector('#job-results textarea').focus()`);
	assert.equal(await evaluate(`document.querySelector('#job-results textarea').selectionEnd`), expectedGoodLinks.length);
	await clickTab('Other jobs');
	await waitFor(selected('Other jobs'));
	assert.equal(await evaluate(`${bulkCopy} === null`), true, 'Other jobs never offers bulk good-match copying');
	assert.equal(await evaluate(`document.querySelector('#job-results textarea') === null`), true, 'Bulk fallback stays scoped to good matches');
	await clickTab('Good matches');
	await waitFor(`document.querySelectorAll('.matches > li').length === 20 && ${selected('Good matches')}`);
	await changePage('Page.reload');
	await waitFor(`document.querySelectorAll('.matches > li').length === 20 && ${selected('Good matches')}`);
	const bulkDesktopShot = await send('Page.captureScreenshot', { format: 'png' });
	await Bun.write(resolve(import.meta.dir, '../../.context/copy-all-links-desktop-fixture.png'), Buffer.from(bulkDesktopShot.data, 'base64'));
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
	await assertSavings('~1h 5m saved', '130 jobs checked');
	assert.equal(await evaluate(`document.querySelector('.matches .time-saved') === null`), true, 'Failed tailoring has no time-saved claim');
	await evaluate(`${firstCard}.querySelector('.job-actions button:last-child').click()`);
	await waitFor(`${firstCard}.innerText.includes('Preparing CV…') && localStorage.getItem('${tailorKey}')?.includes('${artifactId}')`);
	await assertSavings('~1h 5m saved', '130 jobs checked');
	assert.equal(await evaluate(`${firstCard}.querySelector('.time-saved') === null`), true, 'Queued tailoring has no time-saved claim');
	assert.equal(tailorPosts, 1);
	await clickTab('Other jobs');
	await waitFor(selected('Other jobs'));
	await changePage('Page.reload');
	await waitFor(`document.querySelectorAll('.matches > li').length === 15 && ${selected('Other jobs')}`);
	assert.equal(tailorPosts, 1, 'Refresh never creates another tailored CV');
	await assertSavings('~1h 5m saved', '130 jobs checked');
	tailoringReady = true;
	await waitFor(`JSON.parse(localStorage.getItem('${tailorKey}')).entries[0].downloadPending === false`);
	await assertSavings('~1h 15m saved', '130 jobs checked · 1 CV tailored');
	for (let attempt = 0; attempt < 100 && !existsSync(join(downloads, 'Candidate-Engineer.pdf')); attempt++) await sleep(100);
	assert.equal(readFileSync(join(downloads, 'Candidate-Engineer.pdf'), 'utf8'), pdfFixture, 'Tailor action automatically saves an actual PDF');
	assert.equal(pdfDownloads, 1);
	await clickTab('Good matches');
	await waitFor(`${selected('Good matches')} && ${firstCard}.innerText.includes('Download tailored CV')`);
	assert.equal(await evaluate(`${firstCard}.querySelector('.time-saved') === null`), true, 'Savings appear only in the total summary');
	await evaluate(`${firstCard}.querySelector('.job-actions button:last-child').click()`);
	await waitFor(`${firstCard}.querySelector('.job-actions button:last-child').disabled === false`);
	assert.equal(tailorPosts, 2, 'Explicit re-download revalidates the cached version');
	assert.equal(pdfDownloads, 2);
	await assertSavings('~1h 15m saved', '130 jobs checked · 1 CV tailored');
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
	await assertSavings('~1h 15m saved', '130 jobs checked · 1 CV tailored');
	await clickTab('Good matches');
	await waitFor(`document.querySelectorAll('.matches > li').length === 115 && ${selected('Good matches')}`);
	await clickTab('Other jobs');
	await waitFor(`document.querySelectorAll('.matches > li').length === 15 && ${selected('Other jobs')}`);
	const cache = await evaluate(`localStorage.getItem('${historyKey}')`);
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
	await assertSavings('~1h 15m saved', '130 jobs checked · 1 CV tailored');
	const desktopShot = await send('Page.captureScreenshot', { format: 'png' });
	await Bun.write(resolve(import.meta.dir, '../../.context/job-actions-desktop-fixture.png'), Buffer.from(desktopShot.data, 'base64'));
	await Bun.write(resolve(import.meta.dir, '../../.context/time-savings-desktop-fixture.png'), Buffer.from(desktopShot.data, 'base64'));
	await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
	assert.equal(await evaluate(`document.documentElement.scrollWidth <= window.innerWidth`), true, 'Mobile has no horizontal overflow');
	await assertActionRail();
	const shot = await send('Page.captureScreenshot', { format: 'png' });
	await Bun.write(resolve(import.meta.dir, '../../.context/job-actions-mobile-fixture.png'), Buffer.from(shot.data, 'base64'));
	await Bun.write(resolve(import.meta.dir, '../../.context/time-savings-mobile-fixture.png'), Buffer.from(shot.data, 'base64'));
	await clickTab('Good matches');
	await waitFor(`document.querySelectorAll('.matches > li').length === 115 && ${selected('Good matches')}`);
	assert.equal(await evaluate(`document.documentElement.scrollWidth <= window.innerWidth && ${bulkCopy}.getBoundingClientRect().right <= window.innerWidth`), true, 'Bulk action fits mobile without overflow');
	const bulkMobileShot = await send('Page.captureScreenshot', { format: 'png' });
	await Bun.write(resolve(import.meta.dir, '../../.context/copy-all-links-mobile-fixture.png'), Buffer.from(bulkMobileShot.data, 'base64'));
	await send('Emulation.setDeviceMetricsOverride', { width: 320, height: 844, deviceScaleFactor: 1, mobile: true });
	assert.equal(await evaluate(`document.documentElement.scrollWidth <= window.innerWidth`), true, 'Narrow phones have no horizontal overflow');
	await assertActionRail(false);
	await changePage('Page.navigate', { url: base });
	await waitFor(`document.querySelector('.history a') !== null`);
	await evaluate(`[...document.querySelectorAll('button')].find(b => b.textContent === 'Clear list').click()`);
	await waitFor(`localStorage.getItem('${historyKey}') === null && !document.querySelector('.history')`);
	await evaluate(`window.dispatchEvent(new Event('focus'))`);
	await sleep(300);
	assert.equal(await evaluate(`document.querySelector('.history') === null`), true, 'Authoritative discovery does not resurrect locally hidden CVs');
	agentUploaded = true;
	await evaluate(`window.dispatchEvent(new Event('focus'))`);
	await waitFor(`document.querySelector('.history a')?.textContent.includes('Agent-uploaded CV.pdf')`);
	assert.equal(await evaluate(`document.querySelectorAll('.history li').length`), 1, 'A new agent upload appears while hidden CV stays hidden');
	await changePage('Page.navigate', { url: `${base}/connect` });
	await waitFor(`document.querySelector('.primary') && !document.querySelector('.primary').disabled`);
	assert.equal(await evaluate(`document.cookie.includes('avicv_session')`), false, 'Session stays HttpOnly');
	await evaluate(`document.querySelector('.primary').click()`);
	await waitFor(`document.querySelector('#connection-token')?.value === '${tokenSecret}'`);
	assert.equal(await evaluate(`document.querySelector('.connections').innerText.includes('Not used yet')`), true, 'Creating a token never claims the client has connected');
	assert.equal(await evaluate(`JSON.stringify(localStorage).includes('${tokenSecret}')`), false, 'Token is not saved in localStorage');
	assert.equal(await evaluate(`[...document.querySelectorAll('pre')].some(node => node.textContent.includes('--bearer-token-env-var AVICV_TOKEN'))`), true);
	assert.equal(await evaluate(`[...document.querySelectorAll('pre')].some(node => node.textContent.includes('${tokenSecret}'))`), false, 'Setup snippets never contain the bearer secret');
	assert.equal(await evaluate(`document.documentElement.scrollWidth <= window.innerWidth`), true, 'Connection setup fits a narrow phone');
	connection!.last_used_at = '2026-10-12T01:00:00Z';
	await evaluate(`window.dispatchEvent(new Event('focus'))`);
	await waitFor(`document.querySelector('.connections').innerText.includes('Last used')`);
	const connectShot = await send('Page.captureScreenshot', { format: 'png' });
	await Bun.write(resolve(import.meta.dir, '../../.context/agent-connect-mobile-fixture.png'), Buffer.from(connectShot.data, 'base64'));
	await changePage('Page.reload');
	await waitFor(`document.querySelector('.connections') !== null`);
	assert.equal(await evaluate(`document.querySelector('#connection-token') === null`), true, 'Refresh never reveals a previously issued token');
	await evaluate(`document.querySelector('.revoke').click()`);
	await waitFor(`document.querySelector('.connections') === null`);
	assert.equal(connection, null, 'Revoke reaches the authoritative API');
	allMatches = allMatches.map((item, index) => ({ ...item, score: 0.8 - index * 0.001 }));
	sample.resume.status = 'done';
	await changePage('Page.navigate', { url: `${base}/resumes/${'b'.repeat(32)}` });
	await waitFor(`document.querySelectorAll('.matches > li').length === 20 && ${selected('Other jobs')}`);
	assert.equal(await evaluate(`document.body.innerText.includes('No good matches yet')`), true);
	assert.equal(await evaluate(`${tab('Good matches')}.textContent.includes('0')`), true);
	for (const mode of ['unavailable', 'zero'] as const) {
		savingsMode = mode;
		const requestsBefore = savingsRequests;
		await changePage('Page.reload');
		await waitFor(`document.querySelectorAll('.matches > li').length === 20 && ${selected('Other jobs')}`);
		for (let attempt = 0; attempt < 100 && savingsRequests === requestsBefore; attempt++) await sleep(100);
		assert.ok(savingsRequests > requestsBefore, `The ${mode} savings response was requested`);
		await waitFor(`${savings} === null`);
		assert.equal(await evaluate(`document.body.innerText.includes('Savings unavailable')`), false, 'Unavailable savings never introduces an engineering error panel');
	}
	await changePage('Page.navigate', { url: `${base}/resumes/${resumeId}` });
	await waitFor(`${firstCard}?.querySelector('.job-actions button:last-child')?.textContent.includes('Download tailored CV')`);
	const downloadsBeforeRecovery = pdfDownloads, postsBeforeRecovery = tailorPosts;
	pdfUnavailable = true;
	await evaluate(`${firstCard}.querySelector('.job-actions button:last-child').click()`);
	await waitFor(`${firstCard}.innerText.includes('Your CV is ready. Try downloading it again.') && !${firstCard}.querySelector('.job-actions button:last-child').disabled`);
	assert.equal(pdfDownloads, downloadsBeforeRecovery);
	assert.equal(await evaluate(`JSON.parse(localStorage.getItem('${tailorKey}')).entries.find(entry => entry.resumeId === '${resumeId}').downloadPending`), true);
	pdfUnavailable = false;
	await evaluate(`window.dispatchEvent(new Event('focus'))`);
	await waitFor(`JSON.parse(localStorage.getItem('${tailorKey}')).entries.find(entry => entry.resumeId === '${resumeId}').downloadPending === false`);
	assert.equal(pdfDownloads, downloadsBeforeRecovery + 1, 'Discovery resumes the requested download after a temporary PDF failure');
	assert.equal(tailorPosts, postsBeforeRecovery + 1, 'Recovery reuses the artifact without creating another tailoring request');
	assert.deepEqual(exceptions, []);
	assert.equal(readRealEnv(), originalRealEnv, 'The browser test must not modify the running dev server environment');
	console.log('PASS: estimated time saved from successful checks and ready PDFs, transparent assumptions, no inflation after failed/queued requests or repeated downloads/refresh, live tailoring update, zero/unavailable savings hidden; bulk copying all 115 ranked good links beyond visible cards, bulk empty/error/denied states, three actions, direct Apply links, clipboard success and denied fallback, durable tailoring and automatic PDF download after reload/tab switch, recovery after a temporary PDF download failure, version revalidation, category boundary and counts, independent pagination, no percentages or diagnostics, history and preferences, offline/reconnect, mobile layout, zero-good fallback, browser isolation.');

} catch (error) { console.error(logs); throw error; }
finally {
	ws?.close(); chrome.kill(); frontend.kill(); api.stop(true);
	await sleep(200); rmSync(profile, { recursive: true, force: true }); rmSync(isolatedFrontend, { recursive: true, force: true }); rmSync(downloads, { recursive: true, force: true });
}
