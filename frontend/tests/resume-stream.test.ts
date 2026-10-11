import { expect, test } from 'bun:test';
import { streamResume, waitForPoll } from '../src/lib/server/resume-stream.ts';
import { match, result, resumeId } from './fixtures.ts';
const input = { id: resumeId, nearby: true, limit: 100 };

test('forwards location filtering before pagination and stops after partial completion', async () => {
	const sample = result(); sample.resume.status = 'partial';
	const paths: string[] = [];
	const controller = new AbortController();
	const stream = streamResume(input, controller.signal, async (path, init) => {
		expect(init.signal).toBe(controller.signal); paths.push(path);
		return Response.json(path.includes('match-results') ? sample.matches : sample.resume);
	});
	expect((await stream.next()).value).toEqual(sample);
	expect((await stream.next()).done).toBe(true);
	expect(paths[1]).toContain('nearby=true&limit=100&offset=0');
});

test('loads beyond one API page instead of silently truncating at the first 100', async () => {
	const sample = result(); const offsets: number[] = [];
	const stream = streamResume({ ...input, limit: 600 }, new AbortController().signal, async (path) => {
		if (!path.includes('match-results')) return Response.json(sample.resume);
		const params = new URL(path, 'http://test').searchParams;
		const offset = Number(params.get('offset')); const limit = Number(params.get('limit')); offsets.push(offset);
		return Response.json({ ...sample.matches, items: Array.from({ length: limit }, (_, i) => match(offset + i)), total: 1000, has_more: true });
	});
	expect((await stream.next()).value?.matches.items).toHaveLength(600);
	expect(offsets).toEqual([0, 500]);
});

test('abort cancels a sleeping unchanged live query and lets generator.return finish', async () => {
	const sample = result(); sample.resume.status = 'parsing'; sample.resume.progress.total = 0;
	const controller = new AbortController(); let calls = 0; let sleeping!: () => void;
	const paused = new Promise<void>((resolve) => sleeping = resolve);
	const stream = streamResume(input, controller.signal, async () => { calls++; return Response.json(sample.resume); }, async (signal) => { sleeping(); await waitForPoll(signal, 60_000); });
	await stream.next(); const next = stream.next(); await paused;
	controller.abort();
	expect((await next).done).toBe(true);
	expect((await stream.return()).done).toBe(true); expect(calls).toBe(1);
});

test('abort interrupts an in-flight upstream request without surfacing a connection error', async () => {
	const controller = new AbortController(); let started!: () => void;
	const waiting = new Promise<void>((resolve) => started = resolve);
	const stream = streamResume(input, controller.signal, async (_, init) => new Promise<Response>((_, reject) => {
		started(); init.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
	}));
	const next = stream.next(); await waiting; controller.abort();
	expect((await next).done).toBe(true);
});


test('a ranking change between pages cannot produce duplicate card keys', async () => {
	const sample = result();
	const stream = streamResume({ ...input, limit: 600 }, new AbortController().signal, async (path) => {
		if (!path.includes('match-results')) return Response.json(sample.resume);
		const params = new URL(path, 'http://test').searchParams;
		const offset = Number(params.get('offset')); const limit = Number(params.get('limit'));
		const items = Array.from({ length: limit }, (_, i) => match(offset + i));
		if (offset) items[0] = match(499);
		return Response.json({ ...sample.matches, items, total: 1000, has_more: true });
	});
	const items = (await stream.next()).value!.matches.items;
	expect(new Set(items.map((item) => item.job_key)).size).toBe(items.length);
	expect(items).toHaveLength(599);
});
