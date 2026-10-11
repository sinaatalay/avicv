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

/** The browser only downloads through our same-origin endpoint, never an API-provided URL. */
export async function downloadPdf(url: string, signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<void> {
	const response = await fetcher(url, { signal, cache: 'no-store' });
	if (!response.ok || !response.headers.get('content-type')?.toLowerCase().startsWith('application/pdf')) {
		throw new Error('Download unavailable');
	}
	const blob = await response.blob();
	if (signal.aborted) throw signal.reason;
	const filename = response.headers.get('content-disposition')?.match(/filename="([a-zA-Z0-9 ._()-]+\.pdf)"/i)?.[1] ?? 'Tailored-CV.pdf';
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
}

/** One controller per result page keeps work moving when cards or tabs are hidden. */
export function createTailorManager(resumeId: string, onChange: (states: TailorStates) => void, options: Options = {}) {
	const storage = options.storage ?? browserStorage();
	const fetcher = options.fetcher ?? fetch;
	const download = options.download ?? ((url, signal) => downloadPdf(url, signal, fetcher));
	const wait = options.wait ?? pause;
	const base = options.resumePath ?? `/resumes/${resumeId}`;
	const controller = new AbortController();
	const { signal } = controller;
	const references = new Map<string, TailorReference>();
	const running = new Set<string>();
	let states: TailorStates = {};
	const update = (jobKey: string, changes: Partial<TailorJobState>) => {
		if (signal.aborted) return;
		const previous = states[jobKey] ?? { artifact: null, busy: false, error: null, storageWarning: false };
		states = { ...states, [jobKey]: { ...previous, ...changes } };
		onChange(states);
	};
	const persist = (reference: TailorReference) => {
		references.set(reference.jobKey, reference);
		update(reference.jobKey, { storageWarning: !saveTailorReference(reference, storage) });
	};
	const getArtifact = async (path: string, init?: RequestInit): Promise<TailoredCv> => {
		const response = await fetcher(path, { ...init, signal, cache: 'no-store' });
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
	};
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
