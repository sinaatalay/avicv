import * as v from 'valibot';
import type { TailoredCv } from './contracts.ts';
import type { StorageLike } from './resume-history.ts';
import { browserStorage, readTailorHistory, saveTailorReference, type TailorReference } from './tailor-history.ts';

export const tailoredCvSchema = v.object({
	id: v.pipe(v.string(), v.regex(/^[a-f0-9]{32}$/)),
	status: v.picklist(['queued', 'waiting_for_design', 'tailoring', 'rendering', 'ready', 'failed']),
	error: v.nullable(v.string()), download_url: v.nullable(v.string()),
	design_source: v.nullable(v.picklist(['reconstructed', 'default']))
});
const artifactListSchema = v.object({
	items: v.array(v.object({ ...tailoredCvSchema.entries,
		job_key: v.pipe(v.string(), v.minLength(1), v.maxLength(256)), created_at: v.string(), updated_at: v.string()
	})),
	offset: v.number(), limit: v.number(), has_more: v.boolean()
});
export interface TailorJobState {
	artifact: TailoredCv | null;
	busy: boolean;
	error: string | null;
	storageWarning: boolean;
}
export type TailorStates = Record<string, TailorJobState>;

class TailorRequestError extends Error {}

export function pause(ms: number, signal: AbortSignal): Promise<void> {
	return new Promise((resolve, reject) => {
		if (signal.aborted) { reject(signal.reason); return; }
		const abort = () => { clearTimeout(timer); reject(signal.reason); };
		const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
		signal.addEventListener('abort', abort, { once: true });
	});
}

/** Bound both the request and its response body; page disposal still cancels immediately. */
async function withRequestDeadline<T>(parent: AbortSignal, timeoutMs: number, request: (signal: AbortSignal) => Promise<T>): Promise<T> {
	const controller = new AbortController();
	const abort = () => controller.abort(parent.reason);
	parent.addEventListener('abort', abort, { once: true });
	if (parent.aborted) abort();
	const timer = setTimeout(() => controller.abort(new DOMException('Request timed out', 'TimeoutError')), timeoutMs);
	try { return await request(controller.signal); }
	finally { clearTimeout(timer); parent.removeEventListener('abort', abort); }
}

/** The browser only downloads through our same-origin endpoint, never an API-provided URL. */
export async function downloadPdf(url: string, signal: AbortSignal, fetcher: typeof fetch = fetch, timeoutMs = 60_000): Promise<void> {
	const { blob, filename } = await withRequestDeadline(signal, timeoutMs, async (requestSignal) => {
		const response = await fetcher(url, { signal: requestSignal, cache: 'no-store' });
		if (!response.ok || !response.headers.get('content-type')?.toLowerCase().startsWith('application/pdf')) {
			throw new Error('Download unavailable');
		}
		const filename = response.headers.get('content-disposition')?.match(/filename="([a-zA-Z0-9 ._()-]+\.pdf)"/i)?.[1] ?? 'Tailored-CV.pdf';
		return { blob: await response.blob(), filename };
	});
	if (signal.aborted) throw signal.reason;
	const objectUrl = URL.createObjectURL(blob);
	const link = document.createElement('a');
	link.href = objectUrl; link.download = filename; link.style.display = 'none';
	document.body.append(link); link.click(); link.remove();
	// Keep the object alive long enough for browsers to begin saving the file.
	setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

interface Options {
	storage?: StorageLike;
	fetcher?: typeof fetch;
	download?: (url: string, signal: AbortSignal) => Promise<void>;
	wait?: typeof pause;
	resumePath?: string;
	requestTimeoutMs?: number;
}

/** One controller per result page keeps work moving when cards or tabs are hidden. */
export function createTailorManager(resumeId: string, onChange: (states: TailorStates) => void, options: Options = {}) {
	const storage = options.storage ?? browserStorage();
	const fetcher = options.fetcher ?? fetch;
	const download = options.download ?? ((url, signal) => downloadPdf(url, signal, fetcher));
	const wait = options.wait ?? pause;
	const requestTimeoutMs = options.requestTimeoutMs ?? 15_000;
	const base = options.resumePath ?? `/resumes/${resumeId}`;
	const controller = new AbortController();
	const { signal } = controller;
	const references = new Map<string, TailorReference>();
	const running = new Set<string>();
	const revisions = new Map<string, number>();
	let refreshing = false;
	let states: TailorStates = {};
	const update = (jobKey: string, changes: Partial<TailorJobState>) => {
		if (signal.aborted) return;
		const previous = states[jobKey] ?? { artifact: null, busy: false, error: null, storageWarning: false };
		revisions.set(jobKey, (revisions.get(jobKey) ?? 0) + 1);
		states = { ...states, [jobKey]: { ...previous, ...changes } };
		onChange(states);
	};
	const persist = (reference: TailorReference) => {
		references.set(reference.jobKey, reference);
		update(reference.jobKey, { storageWarning: !saveTailorReference(reference, storage) });
	};
	const getArtifact = (path: string, init?: RequestInit): Promise<TailoredCv> => withRequestDeadline(signal, requestTimeoutMs, async (requestSignal) => {
		const response = await fetcher(path, { ...init, signal: requestSignal, cache: 'no-store' });
		if (!response.ok) {
			if (response.status >= 400 && response.status < 500) {
				const body = await response.json().catch(() => null);
				const message = typeof body?.message === 'string' && body.message.length <= 300
					? body.message : 'This CV is unavailable. Try tailoring it again.';
				throw new TailorRequestError(message);
			}
			throw new Error('CV unavailable');
		}
		return v.parse(tailoredCvSchema, await response.json());
	});
	const finish = async (jobKey: string, artifact: TailoredCv) => {
		if (!references.get(jobKey)?.downloadPending) return;
		await download(`${base}/tailored/${artifact.id}/cv.pdf`, signal);
		const reference = references.get(jobKey)!;
		persist({ ...reference, downloadPending: false, updatedAt: Date.now() });
	};
	const watch = async (jobKey: string, initial?: TailoredCv) => {
		let artifact = initial;
		let attempts = 0;
		while (!signal.aborted) {
			if (!artifact) {
				try {
					artifact = await getArtifact(`${base}/tailored/${references.get(jobKey)!.artifactId}`);
					attempts = 0;
				} catch (cause) {
					if (signal.aborted || cause instanceof TailorRequestError || ++attempts >= 3) throw cause;
					await wait(1500 * attempts, signal); continue;
				}
			}
			update(jobKey, { artifact });
			if (artifact.status === 'failed') {
				update(jobKey, { error: 'We couldn’t prepare your CV. Try again.' }); return;
			}
			if (artifact.status === 'ready') { await finish(jobKey, artifact); return; }
			await wait(2000, signal);
			artifact = undefined;
		}
	};
	const run = async (jobKey: string, create: boolean) => {
		if (signal.aborted || running.has(jobKey)) return;
		running.add(jobKey); update(jobKey, { busy: true, error: null });
		try {
			let artifact: TailoredCv | undefined;
			if (create) {
				artifact = await getArtifact(`${base}/tailor`, {
					method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_key: jobKey })
				});
				persist({ resumeId, jobKey, artifactId: artifact.id, downloadPending: true, updatedAt: Date.now() });
			}
			await watch(jobKey, artifact);
		} catch (cause) {
			if (!signal.aborted) update(jobKey, { error: cause instanceof TailorRequestError ? cause.message
				: states[jobKey]?.artifact?.status === 'ready' ? 'Your CV is ready. Try downloading it again.' : 'Couldn’t reach your CV. Try again.' });
		} finally { running.delete(jobKey); update(jobKey, { busy: false }); }
	};
	return {
		async refresh() {
			if (refreshing || signal.aborted) return;
			refreshing = true;
			const seen = new Set<string>();
			try {
				let offset = 0;
				while (!signal.aborted) {
					const previousRevisions = new Map(revisions);
					const list = await withRequestDeadline(signal, requestTimeoutMs, async (requestSignal) => {
						const response = await fetcher(`${base}/tailored?offset=${offset}`, { signal: requestSignal, cache: 'no-store' });
						if (!response.ok) throw new Error('CVs unavailable');
						return v.parse(artifactListSchema, await response.json());
					});
					for (const artifact of list.items) {
						// The authoritative list is newest first. A browser request retains its own download intent.
						if (seen.has(artifact.job_key)) continue;
						seen.add(artifact.job_key);
						if (running.has(artifact.job_key) || revisions.get(artifact.job_key) !== previousRevisions.get(artifact.job_key)) continue;
						const reference = references.get(artifact.job_key);
						if (reference?.artifactId !== artifact.id) persist({
							resumeId, artifactId: artifact.id, jobKey: artifact.job_key,
							downloadPending: false, updatedAt: Date.parse(artifact.updated_at) || Date.now()
						});
						update(artifact.job_key, { artifact, error: artifact.status === 'failed' ? 'We couldn’t prepare your CV. Try again.' : null });
						if (artifact.status !== 'failed' && (artifact.status !== 'ready' || references.get(artifact.job_key)?.downloadPending)) {
							void run(artifact.job_key, false);
						}
					}
					if (!list.has_more || list.limit <= 0 || !list.items.length) break;
					offset = list.offset + list.limit;
				}
			} catch { /* Existing work and explicit download actions report their own actionable errors. */ }
			finally { refreshing = false; }
		},
		restore() {
			for (const reference of readTailorHistory(storage).filter((entry) => entry.resumeId === resumeId)) {
				references.set(reference.jobKey, reference);
				void run(reference.jobKey, false);
			}
		},
		async request(jobKey: string) {
			if (running.has(jobKey) || signal.aborted) return;
			// Revalidate the backend's candidate, job and design versions on every explicit click.
			await run(jobKey, true);
		},
		dispose() { controller.abort(); }
	};
}
