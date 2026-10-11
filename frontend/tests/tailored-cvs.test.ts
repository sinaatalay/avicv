import { describe, expect, test } from 'bun:test';
import type { TailoredCv } from '../src/lib/contracts.ts';
import { createTailorManager, type TailorStates } from '../src/lib/tailored-cvs.ts';
import { forgetResume, type StorageLike } from '../src/lib/resume-history.ts';
import { readTailorHistory, saveTailorReference, TAILOR_HISTORY_KEY } from '../src/lib/tailor-history.ts';
import { tailoredPdfResponse, tailoredStatusResponse } from '../src/lib/server/tailored-response.ts';
import { resumeId } from './fixtures.ts';

class MemoryStorage implements StorageLike {
	data = new Map<string, string>();
	getItem(key: string) { return this.data.get(key) ?? null; }
	setItem(key: string, value: string) { this.data.set(key, value); }
	removeItem(key: string) { this.data.delete(key); }
}
const artifactId = 'c'.repeat(32);
const artifact = (status: TailoredCv['status'] = 'ready'): TailoredCv => ({
	id: artifactId, status, error: null, design_source: 'reconstructed', download_url: 'https://never-follow.example/unsafe.pdf'
});
const reference = (downloadPending = true) => ({ resumeId, jobKey: 'job-1', artifactId, downloadPending, updatedAt: 1 });
const settle = async (condition: () => boolean) => {
	for (let index = 0; index < 100; index++) { if (condition()) return; await Bun.sleep(1); }
	throw new Error('Controller did not settle');
};

describe('tailored CV persistence and lifecycle', () => {
	test('discovers agent-created PDFs across pages without downloading until the user asks', async () => {
		const storage = new MemoryStorage(); let state: TailorStates = {}; const downloads: string[] = [];
		const oldId = 'd'.repeat(32);
		const manager = createTailorManager(resumeId, (next) => state = next, {
			storage,
			fetcher: (async (input, init) => {
				if (init?.method === 'POST') return Response.json(artifact());
				const offset = Number(new URL(String(input), 'https://avicv.com').searchParams.get('offset'));
				return Response.json({ items: [{ ...artifact(), id: offset ? oldId : artifactId, job_key: 'job-1',
					created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }], offset, limit: 1, has_more: offset === 0 });
			}) as typeof fetch,
			download: async (url) => { downloads.push(url); }
		});
		await manager.refresh();
		expect(state['job-1'].artifact?.id).toBe(artifactId);
		expect(state['job-1'].busy).toBe(false);
		expect(readTailorHistory(storage)[0].downloadPending).toBe(false);
		expect(downloads).toEqual([]);
		await manager.request('job-1');
		expect(downloads).toEqual([`/resumes/${resumeId}/tailored/${artifactId}/cv.pdf`]);
		manager.dispose();
	});

	test('an agent-created pending PDF completes without creating browser download intent', async () => {
		const storage = new MemoryStorage(); let state: TailorStates = {}; let downloads = 0;
		const manager = createTailorManager(resumeId, (next) => state = next, {
			storage,
			fetcher: (async (input) => String(input).includes('?offset=')
				? Response.json({ items: [{ ...artifact('rendering'), job_key: 'job-1', created_at: '2026-01-01', updated_at: '2026-01-01' }], offset: 0, limit: 100, has_more: false })
				: Response.json(artifact())) as typeof fetch,
			download: async () => { downloads++; }
		});
		await manager.refresh(); await settle(() => state['job-1']?.busy === false);
		expect(state['job-1'].artifact?.status).toBe('ready');
		expect(downloads).toBe(0);
		expect(readTailorHistory(storage)[0].downloadPending).toBe(false);
		manager.dispose();
	});

	test('stores bounded references only, rejects corruption and clears references with their resume', () => {
		const storage = new MemoryStorage();
		for (let index = 0; index < 205; index++) saveTailorReference({ ...reference(), jobKey: `job-${index}`, cv: 'private@example.com', pdf: 'binary' } as ReturnType<typeof reference>, storage);
		expect(readTailorHistory(storage)).toHaveLength(200);
		expect(storage.getItem(TAILOR_HISTORY_KEY)).not.toContain('private');
		expect(storage.getItem(TAILOR_HISTORY_KEY)).not.toContain('binary');
		forgetResume(resumeId, storage);
		expect(readTailorHistory(storage)).toEqual([]);
		storage.setItem(TAILOR_HISTORY_KEY, '{bad');
		expect(readTailorHistory(storage)).toEqual([]);
		expect(saveTailorReference({ ...reference(), artifactId: '../unsafe' }, storage)).toBe(false);
	});

	test('persists intent before waiting; reload resumes with GET and downloads exactly once', async () => {
		const storage = new MemoryStorage();
		const calls: string[] = [];
		let state: TailorStates = {};
		const first = createTailorManager(resumeId, (next) => state = next, {
			storage,
			fetcher: (async (_input, init) => { calls.push(init?.method ?? 'GET'); return Response.json(artifact('queued')); }) as typeof fetch,
			wait: async (_ms, signal) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }))
		});
		const request = first.request('job-1');
		await settle(() => readTailorHistory(storage).length === 1);
		await first.request('job-1');
		expect(calls).toEqual(['POST']);
		expect(readTailorHistory(storage)[0].downloadPending).toBe(true);
		first.dispose(); await request;
		const downloads: string[] = [];
		const second = createTailorManager(resumeId, (next) => state = next, {
			storage,
			fetcher: (async (_input, init) => { calls.push(init?.method ?? 'GET'); return Response.json(artifact()); }) as typeof fetch,
			download: async (url) => { downloads.push(url); }
		});
		second.restore();
		await settle(() => state['job-1']?.busy === false);
		expect(calls).toEqual(['POST', 'GET']);
		expect(downloads).toEqual([`/resumes/${resumeId}/tailored/${artifactId}/cv.pdf`]);
		expect(readTailorHistory(storage)[0].downloadPending).toBe(false);
		second.dispose();
		const third = createTailorManager(resumeId, (next) => state = next, {
			storage, fetcher: (async () => Response.json(artifact())) as typeof fetch,
			download: async (url) => { downloads.push(url); }
		});
		third.restore(); await settle(() => state['job-1']?.busy === false);
		expect(downloads).toHaveLength(1);
		third.dispose();
	});

	test('explicit download revalidates current backend versions instead of serving a stale browser reference', async () => {
		const storage = new MemoryStorage(); saveTailorReference(reference(false), storage);
		let state: TailorStates = {}; const calls: string[] = []; const downloads: string[] = [];
		const nextId = 'd'.repeat(32);
		const manager = createTailorManager(resumeId, (next) => state = next, {
			storage,
			fetcher: (async (_input, init) => {
				calls.push(init?.method ?? 'GET');
				return Response.json({ ...artifact(), id: init?.method === 'POST' ? nextId : artifactId });
			}) as typeof fetch,
			download: async (url) => { downloads.push(url); }
		});
		manager.restore(); await settle(() => state['job-1']?.busy === false);
		await manager.request('job-1');
		expect(calls).toEqual(['GET', 'POST']);
		expect(downloads).toEqual([`/resumes/${resumeId}/tailored/${nextId}/cv.pdf`]);
		manager.dispose();
	});

	test('failed rendering can be retried and a failed download retains pending intent', async () => {
		const storage = new MemoryStorage(); let state: TailorStates = {}; let requests = 0;
		const manager = createTailorManager(resumeId, (next) => state = next, {
			storage,
			fetcher: (async () => Response.json(artifact(++requests === 1 ? 'failed' : 'ready'))) as typeof fetch,
			download: async () => { throw new Error('Offline'); }
		});
		await manager.request('job-1');
		expect(state['job-1'].error).toContain('prepare'); expect(state['job-1'].busy).toBe(false);
		await manager.request('job-1');
		expect(requests).toBe(2);
		expect(state['job-1'].error).toContain('downloading'); expect(state['job-1'].busy).toBe(false);
		expect(readTailorHistory(storage)[0].downloadPending).toBe(true);
		manager.dispose();
	});

	test('aborts in-flight browser requests without a backend cancellation request', async () => {
		const storage = new MemoryStorage(); saveTailorReference(reference(), storage);
		const calls: string[] = []; let signal: AbortSignal | undefined;
		const manager = createTailorManager(resumeId, () => {}, { storage, fetcher: (async (_input, init) => {
			calls.push(init?.method ?? 'GET'); signal = init?.signal as AbortSignal;
			return new Promise((_resolve, reject) => signal!.addEventListener('abort', () => reject(signal!.reason), { once: true }));
		}) as typeof fetch });
		manager.restore(); manager.dispose();
		expect(signal?.aborted).toBe(true);
		expect(calls).toEqual(['GET']);
		expect(readTailorHistory(storage)[0].downloadPending).toBe(true);
	});

	test('shows actionable request errors and does not retry permanent failures automatically', async () => {
		let state: TailorStates = {}; let requests = 0;
		const storage = new MemoryStorage(); saveTailorReference(reference(), storage);
		const manager = createTailorManager(resumeId, (next) => state = next, {
			storage, fetcher: (async () => {
				requests++; return Response.json({ message: 'This job’s description is unavailable. Choose another job.' }, { status: 422 });
			}) as typeof fetch
		});
		manager.restore(); await settle(() => state['job-1']?.busy === false);
		expect(requests).toBe(1);
		expect(state['job-1'].error).toContain('Choose another job');
		manager.dispose();
	});
});

describe('same-origin tailored CV proxy', () => {
	test('streams a private PDF with a safe server filename and no unrelated headers', async () => {
		const upstream = new Response('%PDF-fixture', { headers: {
			'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="Candidate-Role.pdf"', 'Set-Cookie': 'backend=secret'
		} });
		const response = tailoredPdfResponse(upstream);
		expect(response.body).toBe(upstream.body);
		expect(response.headers.get('Content-Disposition')).toBe('attachment; filename="Candidate-Role.pdf"');
		expect(response.headers.get('Cache-Control')).toBe('private, no-store');
		expect(response.headers.has('Set-Cookie')).toBe(false);
		expect(await response.text()).toBe('%PDF-fixture');
	});
	test('rejects wrong content and sanitizes filenames and status URLs', async () => {
		expect(tailoredPdfResponse(new Response('<html>Failure</html>', { headers: { 'Content-Type': 'text/html' } })).status).toBe(502);
		const pdf = tailoredPdfResponse(new Response('%PDF', { headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="../unsafe.pdf"' } }));
		expect(pdf.headers.get('Content-Disposition')).toBe('attachment; filename="Tailored-CV.pdf"');
		const response = await tailoredStatusResponse(Response.json(artifact(), { status: 202 }), `/resumes/${resumeId}`);
		expect(response.status).toBe(202);
		expect((await response.json()).download_url).toBe(`/resumes/${resumeId}/tailored/${artifactId}/cv.pdf`);
		const failure = await tailoredStatusResponse(Response.json({ detail: 'The CV queue is full. Please try again soon.' }, { status: 429 }), `/resumes/${resumeId}`);
		expect(failure.status).toBe(429);
		expect((await failure.json()).message).toContain('queue is full');
	});
});
