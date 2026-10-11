import { describe, expect, test } from 'bun:test';
import { cachedMatches, clearHistory, forgetResume, HISTORY_KEY, readHistory, saveVisit, type StorageLike } from '../src/lib/resume-history.ts';
import type { ViewPreferences } from '../src/lib/resume-history.ts';
import { match, result, resumeId } from './fixtures.ts';
const preferences = (category: 'good' | 'other' = 'other'): ViewPreferences => ({ category, categoryChosen: true, limits: { good: 20, other: 40 } });
class MemoryStorage implements StorageLike {
	data = new Map<string, string>();
	getItem(key: string) { return this.data.get(key) ?? null; }
	setItem(key: string, value: string) { this.data.set(key, value); }
	removeItem(key: string) { this.data.delete(key); }
}
describe('browser persistence', () => {
	test('reopens result links, preferences and a bounded preview without CV/contact data', () => {
		const storage = new MemoryStorage();
		const data = result(); data.matches.items = Array.from({ length: 120 }, (_, i) => match(i));
		expect(saveVisit(resumeId, preferences(), data, storage).warning).toBeNull();
		const restored = readHistory(storage).entries[0];
		expect(restored.id).toBe(resumeId); expect(restored.category).toBe('other'); expect(restored.limits.other).toBe(40); expect(restored.categoryChosen).toBe(true);
		expect(restored.snapshot?.items).toHaveLength(100);
		const raw = storage.getItem(HISTORY_KEY)!;
		expect(raw).not.toContain('private'); expect(raw).not.toContain('Private');
		expect(raw).not.toContain('rendercv_yaml'); expect(raw).not.toContain('filename');
	});
	test('preserves source health while accepting previews saved before those fields existed', () => {
		const storage = new MemoryStorage();
		const data = result();
		saveVisit(resumeId, preferences(), data, storage);
		expect(readHistory(storage).entries[0].snapshot?.items[0].source_stale).toBeUndefined();
		const health = { source_status: 'failed', source_verified_at: '2026-01-01T00:00:00Z', source_stale: true };
		Object.assign(data.matches.items[0], health);
		expect(saveVisit(resumeId, preferences(), data, storage).warning).toBeNull();
		expect(readHistory(storage).entries[0].snapshot?.items[0]).toMatchObject(health);
	});
	test('changing categories never reuses a preview from the wrong tab', () => {
		const storage = new MemoryStorage(); saveVisit(resumeId, preferences('other'), result(), storage);
		saveVisit(resumeId, preferences('good'), undefined, storage);
		const entry = readHistory(storage).entries[0];
		expect(entry.category).toBe('good');
		expect(cachedMatches(entry, 'good')).toEqual([]);
		expect(cachedMatches(entry, 'other')).toHaveLength(1);
	});
	test('legacy mixed previews obey the strict good-match boundary', () => {
		const storage = new MemoryStorage(); const data = result();
		data.matches.items = [ { ...match(1), score: 0.8 }, { ...match(2), score: 0.81 }, { ...match(3), score: 0.2 } ];
		saveVisit(resumeId, preferences(), data, storage);
		const legacy = JSON.parse(storage.getItem(HISTORY_KEY)!);
		delete legacy.entries[0].category; delete legacy.entries[0].categoryChosen; delete legacy.entries[0].limits;
		delete legacy.entries[0].snapshot.category; delete legacy.entries[0].snapshot.goodTotal; delete legacy.entries[0].snapshot.otherTotal;
		storage.setItem(HISTORY_KEY, JSON.stringify(legacy));
		const entry = readHistory(storage).entries[0];
		expect(entry.category).toBe('all'); expect(entry.limits).toEqual({ good: 20, other: 20 });
		expect(cachedMatches(entry, 'good').map((job) => job.job_id)).toEqual([2]);
		expect(cachedMatches(entry, 'other').map((job) => job.job_id)).toEqual([1, 3]);
	});
	test('rejects corrupted, future-version and unsafe cached URLs without crashing', () => {
		const storage = new MemoryStorage();
		for (const raw of ['{bad', JSON.stringify({ version: 99, entries: [] })]) {
			storage.setItem(HISTORY_KEY, raw);
			expect(readHistory(storage).entries).toEqual([]); expect(readHistory(storage).warning).not.toBeNull();
		}
		const unsafe = result(); unsafe.matches.items[0].url = 'javascript:alert(1)';
		expect(saveVisit(resumeId, preferences(), unsafe, storage).warning).not.toBeNull();
	});
	test('quota failure falls back to durable links and preferences', () => {
		const storage = new MemoryStorage(); const set = storage.setItem.bind(storage);
		storage.setItem = (key, value) => { if (value.length > 500) throw new Error('Quota'); set(key, value); };
		const saved = saveVisit(resumeId, preferences(), result(), storage);
		expect(saved.warning).toContain('no room');
		expect(readHistory(storage).entries[0]).toMatchObject({ id: resumeId, category: 'other', snapshot: null });
	});
	test('blocked storage reports failure without throwing', () => {
		const storage: StorageLike = { getItem() { throw new Error('Denied'); }, setItem() { throw new Error('Denied'); }, removeItem() { throw new Error('Denied'); } };
		expect(saveVisit(resumeId, preferences(), result(), storage).warning).toContain('Could not save');
		expect(clearHistory(storage).warning).toContain('Could not clear');
		expect(readHistory().entries).toEqual([]);
	});
	test('merges visits, bounds history and removes only the requested data', () => {
		const storage = new MemoryStorage();
		for (let i = 0; i < 12; i++) saveVisit(i.toString(16).padStart(32, '0'), preferences(), undefined, storage);
		expect(readHistory(storage).entries).toHaveLength(10);
		const first = readHistory(storage).entries[0].id;
		forgetResume(first, storage); expect(readHistory(storage).entries).toHaveLength(9);
		clearHistory(storage); expect(storage.getItem(HISTORY_KEY)).toBeNull();
	});
});
