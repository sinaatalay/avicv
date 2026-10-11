import * as v from 'valibot';
import type { StorageLike } from './resume-history.ts';

export const TAILOR_HISTORY_KEY = 'speedhack.tailored-cvs.v1';
const MAX_ARTIFACTS = 200;
const id = v.pipe(v.string(), v.regex(/^[a-f0-9]{32}$/));
const referenceSchema = v.object({
	resumeId: id, artifactId: id,
	jobKey: v.pipe(v.string(), v.minLength(1), v.maxLength(256)),
	downloadPending: v.boolean(),
	updatedAt: v.pipe(v.number(), v.minValue(0))
});
const historySchema = v.object({ version: v.literal(1), entries: v.pipe(v.array(referenceSchema), v.maxLength(MAX_ARTIFACTS)) });
export type TailorReference = v.InferOutput<typeof referenceSchema>;

export function browserStorage(): StorageLike | undefined {
	try { return typeof window === 'undefined' ? undefined : window.localStorage; }
	catch { return undefined; }
}

/** Only opaque server references and download intent are kept on this device. */
export function readTailorHistory(storage = browserStorage()): TailorReference[] {
	try {
		const raw = storage?.getItem(TAILOR_HISTORY_KEY);
		if (!raw || raw.length > 500_000) return [];
		const parsed = v.safeParse(historySchema, JSON.parse(raw));
		return parsed.success ? parsed.output.entries : [];
	} catch { return []; }
}

export function saveTailorReference(reference: TailorReference, storage = browserStorage()): boolean {
	const parsed = v.safeParse(referenceSchema, reference);
	if (!parsed.success || !storage) return false;
	const entries = [parsed.output, ...readTailorHistory(storage).filter((entry) =>
		entry.resumeId !== reference.resumeId || entry.jobKey !== reference.jobKey)].slice(0, MAX_ARTIFACTS);
	try { storage.setItem(TAILOR_HISTORY_KEY, JSON.stringify({ version: 1, entries })); return true; }
	catch { return false; }
}

export function forgetTailoredCvs(resumeId?: string, storage = browserStorage()): void {
	try {
		if (!resumeId) storage?.removeItem(TAILOR_HISTORY_KEY);
		else storage?.setItem(TAILOR_HISTORY_KEY, JSON.stringify({
			version: 1, entries: readTailorHistory(storage).filter((entry) => entry.resumeId !== resumeId)
		}));
	} catch { /* The result page already reports unavailable browser storage. */ }
}
