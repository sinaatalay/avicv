import * as v from 'valibot';
import type { JobTab, Match, MatchCategory, ResumeResults } from './contracts.ts';
import { forgetTailoredCvs } from './tailor-history.ts';

export const HISTORY_KEY = 'speedhack.resume-history.v1';
const MAX_HISTORY = 10;
const MAX_CACHED_MATCHES = 100;
const text = v.pipe(v.string(), v.maxLength(2000));
const count = v.pipe(v.number(), v.integer(), v.minValue(0));
const score = v.pipe(v.number(), v.minValue(0), v.maxValue(1));
const nullableText = v.nullable(text);
const matchSchema = v.object({
	job_id: count, job_key: text, title: text, company: nullableText,
	locations: v.pipe(v.array(text), v.maxLength(100)), workplace_type: nullableText,
	url: v.nullable(v.pipe(text, v.url(), v.check((url) => /^https?:\/\//i.test(url)))),
	salary_min: v.nullable(v.number()), salary_max: v.nullable(v.number()), salary_currency: nullableText,
	salary_interval: nullableText, posted_at: nullableText, score, fit: v.pipe(v.number(), v.minValue(0), v.maxValue(4)),
	interview: score, must_haves: score, location: v.nullable(score), seniority: v.picklist(['below', 'matches', 'above']),
	catalog_status: v.picklist(['open', 'closed', 'changed', 'missing', 'unverified']),
	provider: nullableText, board: nullableText, source_id: nullableText,
	source_status: v.optional(text), source_verified_at: v.optional(nullableText), source_stale: v.optional(v.boolean())
});
const statusSchema = v.picklist(['queued', 'parsing', 'matching', 'waiting_for_jobs', 'done', 'partial', 'failed']);
const categorySchema = v.picklist(['all', 'good', 'other']);
const pageLimit = v.pipe(count, v.minValue(1), v.maxValue(10000));
const snapshotSchema = v.object({
	category: v.optional(categorySchema, 'all'),
	goodTotal: v.optional(count),
	otherTotal: v.optional(count),
	status: statusSchema,
	progress: v.object({ done: count, total: count, attempted: count, succeeded: count, failed: count, pending: count }),
	total: count, unfilteredTotal: count,
	items: v.pipe(v.array(matchSchema), v.maxLength(MAX_CACHED_MATCHES))
});
const entrySchema = v.object({
	id: v.pipe(v.string(), v.regex(/^[a-f0-9]{32}$/)),
	updatedAt: v.pipe(v.number(), v.minValue(0)),
	nearbyOnly: v.optional(v.boolean(), false),
	category: v.optional(categorySchema, 'all'),
	categoryChosen: v.optional(v.boolean(), false),
	limits: v.optional(v.object({ good: pageLimit, other: pageLimit }), { good: 20, other: 20 }),
	limit: v.optional(pageLimit, 20),
	snapshot: v.nullable(snapshotSchema)
});
const historySchema = v.object({ version: v.literal(1), entries: v.pipe(v.array(entrySchema), v.maxLength(MAX_HISTORY)) });
export type HistoryEntry = v.InferOutput<typeof entrySchema>;
export interface HistoryResult { entries: HistoryEntry[]; warning: string | null }
export type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

function browserStorage(): StorageLike | undefined {
	try { return typeof window === 'undefined' ? undefined : window.localStorage; }
	catch { return undefined; }
}

/** SSR-safe, bounded and validated. Never trust URLs or shapes read from browser storage. */
export function readHistory(storage = browserStorage()): HistoryResult {
	if (!storage) return { entries: [], warning: typeof window === 'undefined' ? null : 'Could not save on this device. Bookmark this page to keep your jobs.' };
	try {
		const raw = storage.getItem(HISTORY_KEY);
		if (!raw) return { entries: [], warning: null };
		if (raw.length > 2_000_000) throw new Error('Oversized history');
		const parsed = v.safeParse(historySchema, JSON.parse(raw));
		if (!parsed.success) throw new Error('Invalid history');
		return { entries: parsed.output.entries, warning: null };
	} catch {
		return { entries: [], warning: 'Could not load your saved history. Your job links still work.' };
	}
}

function writeHistory(entries: HistoryEntry[], storage: StorageLike | undefined): HistoryResult {
	if (!storage) return { entries, warning: 'Could not save on this device. Bookmark this page to keep your jobs.' };
	try {
		storage.setItem(HISTORY_KEY, JSON.stringify({ version: 1, entries }));
		return { entries, warning: null };
	} catch {
		// Retain links and preferences even when quota prevents caching result cards.
		const compact = entries.map((entry) => ({ ...entry, snapshot: null }));
		try {
			storage.setItem(HISTORY_KEY, JSON.stringify({ version: 1, entries: compact }));
			return { entries: compact, warning: 'Your job links are saved. This device has no room for offline previews.' };
		} catch {
			return { entries, warning: 'Could not save on this device. Bookmark this page to keep your jobs.' };
		}
	}
}

export interface ViewPreferences {
	category: JobTab;
	categoryChosen: boolean;
	limits: { good: number; other: number };
}

export function saveVisit(id: string, preferences: ViewPreferences, results?: ResumeResults, storage = browserStorage()): HistoryResult {
	const previous = readHistory(storage).entries;
	const existing = previous.find((entry) => entry.id === id);
	const snapshot = results ? {
		category: preferences.category,
		status: results.resume.status, progress: results.resume.progress,
		total: results.matches.total, unfilteredTotal: results.matches.unfiltered_total,
		goodTotal: results.matches.good_total, otherTotal: results.matches.other_total,
		items: results.matches.items.slice(0, MAX_CACHED_MATCHES)
	} : existing?.snapshot ?? null;
	const parsed = v.safeParse(entrySchema, {
		id, nearbyOnly: false, ...preferences, limit: preferences.limits[preferences.category], snapshot, updatedAt: Date.now()
	});
	if (!parsed.success) return { entries: previous, warning: 'Could not save this preview. Your jobs are still available at this link.' };
	return writeHistory([parsed.output, ...previous.filter((entry) => entry.id !== id)].slice(0, MAX_HISTORY), storage);
}

export function forgetResume(id: string, storage = browserStorage()): HistoryResult {
	forgetTailoredCvs(id, storage);
	return writeHistory(readHistory(storage).entries.filter((entry) => entry.id !== id), storage);
}

export function clearHistory(storage = browserStorage()): HistoryResult {
	if (!storage) return { entries: [], warning: 'Browser storage is unavailable. Could not clear saved history.' };
	try { storage?.removeItem(HISTORY_KEY); forgetTailoredCvs(undefined, storage); return { entries: [], warning: null }; }
	catch { return { entries: readHistory(storage).entries, warning: 'Could not clear browser history. Check your browser storage settings.' }; }
}

/** Old snapshots contain mixed categories. Apply the same strict boundary before displaying them. */
export function cachedMatches(entry: HistoryEntry | undefined, category: MatchCategory): Match[] {
	const snapshot = entry?.snapshot;
	if (!snapshot || (snapshot.category !== 'all' && snapshot.category !== category)) return [];
	return snapshot.items.filter((match) => category === 'all' || (category === 'good' ? match.score > 0.8 : match.score <= 0.8));
}
