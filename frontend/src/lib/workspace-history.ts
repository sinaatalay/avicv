import * as v from 'valibot';
import { HISTORY_KEY, readHistory, type HistoryEntry, type StorageLike } from './resume-history.ts';
import { browserStorage, forgetTailoredCvs, readTailorHistory, saveTailorReference } from './tailor-history.ts';

const id = v.pipe(v.string(), v.regex(/^[a-f0-9]{32}$/));
export const ownedResumesSchema = v.object({
	items: v.array(v.object({
		id, filename: v.string(), status: v.picklist(['queued', 'parsing', 'matching', 'waiting_for_jobs', 'done', 'partial', 'failed']),
		created_at: v.string(), good_total: v.pipe(v.number(), v.integer(), v.minValue(0))
	})),
	total: v.number(), offset: v.number(), limit: v.number(), has_more: v.boolean()
});
export type OwnedResume = v.InferOutput<typeof ownedResumesSchema>['items'][number];
export type RecentResume = HistoryEntry & { server?: OwnedResume };
const HIDDEN_KEY = 'avicv.hidden-resumes.v1';
const unavailable: StorageLike = {
	getItem: () => null,
	setItem: () => { throw new Error('Browser storage unavailable'); },
	removeItem: () => { throw new Error('Browser storage unavailable'); }
};

/** A changed session must never reveal the previous owner's cached CVs. */
export function workspaceStorage(workspaceId: string | null, storage = browserStorage()): StorageLike {
	if (!workspaceId || !/^[a-f0-9]{32}$/.test(workspaceId) || !storage) return unavailable;
	const prefix = `avicv.workspace.${workspaceId}.`;
	return {
		getItem: (key) => storage.getItem(prefix + key),
		setItem: (key, value) => storage.setItem(prefix + key, value),
		removeItem: (key) => storage.removeItem(prefix + key)
	};
}

export function hiddenResumeIds(storage: StorageLike): Set<string> {
	try {
		const raw = storage.getItem(HIDDEN_KEY);
		if (!raw || raw.length > 500_000) return new Set();
		const value = v.safeParse(v.pipe(v.array(id), v.maxLength(10000)), JSON.parse(raw));
		return new Set(value.success ? value.output : []);
	} catch { return new Set(); }
}

export function hideResumes(ids: string[], storage: StorageLike): boolean {
	const hidden = hiddenResumeIds(storage);
	for (const value of ids) if (/^[a-f0-9]{32}$/.test(value)) hidden.add(value);
	try { storage.setItem(HIDDEN_KEY, JSON.stringify([...hidden].slice(-10000))); return true; }
	catch { return false; }
}

export function recentResumes(history: HistoryEntry[], owned: OwnedResume[], hidden: Set<string>): RecentResume[] {
	const entries = new Map<string, RecentResume>(history.map((entry) => [entry.id, entry]));
	for (const server of owned) {
		const old = entries.get(server.id);
		entries.set(server.id, {
			id: server.id, updatedAt: Date.parse(server.created_at) || 0, nearbyOnly: false,
			category: 'good', categoryChosen: false, limits: { good: 20, other: 20 }, limit: 20, snapshot: null,
			...old, server
		});
	}
	return [...entries.values()].filter((entry) => !hidden.has(entry.id)).sort((a, b) => b.updatedAt - a.updatedAt);
}

/** Preserve earlier caches only after the server confirms these CVs belong to this owner. */
export function rememberOwnedResumes(owned: OwnedResume[], storage: StorageLike, legacy = browserStorage(), complete = false): void {
	const ownedIds = new Set(owned.map((resume) => resume.id));
	const saved = readHistory(storage).entries;
	const current = complete ? saved.filter((entry) => ownedIds.has(entry.id)) : saved;
	if (complete) for (const entry of readTailorHistory(storage)) {
		if (!ownedIds.has(entry.resumeId)) forgetTailoredCvs(entry.resumeId, storage);
	}
	const ids = new Set(current.map((entry) => entry.id));
	const authorizedLegacy = readHistory(legacy).entries.filter((entry) => ownedIds.has(entry.id) && !ids.has(entry.id));
	const entries = recentResumes([...current, ...authorizedLegacy], owned, hiddenResumeIds(storage))
		.slice(0, 10).map(({ server: _server, ...entry }) => entry);
	try { storage.setItem(HISTORY_KEY, JSON.stringify({ version: 1, entries })); } catch { /* Existing history UI reports storage failures. */ }
	const tailored = new Set(readTailorHistory(storage).map((entry) => `${entry.resumeId}:${entry.jobKey}`));
	for (const entry of readTailorHistory(legacy)) {
		if (ownedIds.has(entry.resumeId) && !tailored.has(`${entry.resumeId}:${entry.jobKey}`)) {
			// Old intent cannot trigger a surprise download in a new session.
			saveTailorReference({ ...entry, downloadPending: false }, storage);
		}
	}
}
