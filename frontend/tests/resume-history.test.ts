import { describe, expect, test } from 'bun:test';
import { clearHistory, forgetResume, HISTORY_KEY, readHistory, saveVisit, type StorageLike } from '../src/lib/resume-history.ts';
import { match, result, resumeId } from './fixtures.ts';
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
		expect(saveVisit(resumeId, true, data, storage).warning).toBeNull();
		const restored = readHistory(storage).entries[0];
		expect(restored.id).toBe(resumeId); expect(restored.nearbyOnly).toBe(true);
		expect(restored.snapshot?.items).toHaveLength(100);
		const raw = storage.getItem(HISTORY_KEY)!;
		expect(raw).not.toContain('private'); expect(raw).not.toContain('Private');
		expect(raw).not.toContain('rendercv_yaml'); expect(raw).not.toContain('filename');
	});
	test('preserves source health while accepting previews saved before those fields existed', () => {
		const storage = new MemoryStorage();
		const data = result();
		saveVisit(resumeId, false, data, storage);
		expect(readHistory(storage).entries[0].snapshot?.items[0].source_stale).toBeUndefined();
		const health = { source_status: 'failed', source_verified_at: '2026-01-01T00:00:00Z', source_stale: true };
		Object.assign(data.matches.items[0], health);
		expect(saveVisit(resumeId, false, data, storage).warning).toBeNull();
		expect(readHistory(storage).entries[0].snapshot?.items[0]).toMatchObject(health);
	});
	test('changing filters drops an incompatible offline preview', () => {
		const storage = new MemoryStorage(); saveVisit(resumeId, false, result(), storage);
		saveVisit(resumeId, true, undefined, storage);
		expect(readHistory(storage).entries[0].snapshot).toBeNull();
	});
	test('rejects corrupted, future-version and unsafe cached URLs without crashing', () => {
		const storage = new MemoryStorage();
		for (const raw of ['{bad', JSON.stringify({ version: 99, entries: [] })]) {
			storage.setItem(HISTORY_KEY, raw);
			expect(readHistory(storage).entries).toEqual([]); expect(readHistory(storage).warning).not.toBeNull();
		}
		const unsafe = result(); unsafe.matches.items[0].url = 'javascript:alert(1)';
		expect(saveVisit(resumeId, false, unsafe, storage).warning).not.toBeNull();
	});
	test('quota failure falls back to durable links and preferences', () => {
		const storage = new MemoryStorage(); const set = storage.setItem.bind(storage);
		storage.setItem = (key, value) => { if (value.length > 500) throw new Error('Quota'); set(key, value); };
		const saved = saveVisit(resumeId, true, result(), storage);
		expect(saved.warning).toContain('storage is full');
		expect(readHistory(storage).entries[0]).toMatchObject({ id: resumeId, nearbyOnly: true, snapshot: null });
	});
	test('blocked storage reports failure without throwing', () => {
		const storage: StorageLike = { getItem() { throw new Error('Denied'); }, setItem() { throw new Error('Denied'); }, removeItem() { throw new Error('Denied'); } };
		expect(saveVisit(resumeId, false, result(), storage).warning).toContain('Could not save');
		expect(clearHistory(storage).warning).toContain('Could not clear');
		expect(readHistory().entries).toEqual([]);
	});
	test('merges visits, bounds history and removes only the requested data', () => {
		const storage = new MemoryStorage();
		for (let i = 0; i < 12; i++) saveVisit(i.toString(16).padStart(32, '0'), false, undefined, storage);
		expect(readHistory(storage).entries).toHaveLength(10);
		const first = readHistory(storage).entries[0].id;
		forgetResume(first, storage); expect(readHistory(storage).entries).toHaveLength(9);
		clearHistory(storage); expect(storage.getItem(HISTORY_KEY)).toBeNull();
	});
});
