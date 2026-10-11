import { expect, test } from 'bun:test';
import { readHistory, saveVisit, type StorageLike } from '../src/lib/resume-history.ts';
import { readTailorHistory, saveTailorReference } from '../src/lib/tailor-history.ts';
import { hideResumes, hiddenResumeIds, recentResumes, rememberOwnedResumes, workspaceStorage, type OwnedResume } from '../src/lib/workspace-history.ts';
import { agentSetup } from '../src/lib/agent-connections.ts';
import { resumeId, result } from './fixtures.ts';

class MemoryStorage implements StorageLike {
	data = new Map<string, string>();
	getItem(key: string) { return this.data.get(key) ?? null; }
	setItem(key: string, value: string) { this.data.set(key, value); }
	removeItem(key: string) { this.data.delete(key); }
}
const preferences = { category: 'good' as const, categoryChosen: true, limits: { good: 20, other: 20 } };
const owned = (id = resumeId): OwnedResume => ({ id, filename: 'private-name.pdf', status: 'done', created_at: '2026-01-01T00:00:00Z', good_total: 5 });

test('browser cache partitions keep another session’s CVs and pending downloads isolated', () => {
	const browser = new MemoryStorage();
	const first = workspaceStorage('1'.repeat(32), browser), second = workspaceStorage('2'.repeat(32), browser);
	saveVisit(resumeId, preferences, result(), first);
	saveTailorReference({ resumeId, jobKey: '1', artifactId: 'c'.repeat(32), downloadPending: true, updatedAt: 1 }, first);
	expect(readHistory(first).entries).toHaveLength(1);
	expect(readHistory(second).entries).toEqual([]);
	expect(readTailorHistory(second)).toEqual([]);
	expect(readHistory(workspaceStorage(null, browser)).entries).toEqual([]);
	expect(readTailorHistory(first)[0].downloadPending).toBe(true);
});

test('agent uploads appear with server totals, while a locally hidden CV stays hidden after refresh', () => {
	const storage = new MemoryStorage();
	rememberOwnedResumes([owned()], storage);
	expect(recentResumes(readHistory(storage).entries, [owned()], hiddenResumeIds(storage))[0].server?.good_total).toBe(5);
	expect(hideResumes([resumeId], storage)).toBe(true);
	rememberOwnedResumes([owned()], storage);
	expect(recentResumes(readHistory(storage).entries, [owned()], hiddenResumeIds(storage))).toEqual([]);
	expect(JSON.stringify([...storage.data.values()])).not.toContain('private-name.pdf');
});

test('legacy caches migrate only for server-confirmed owned CVs, never carry automatic download intent', () => {
	const legacy = new MemoryStorage(), scoped = workspaceStorage('1'.repeat(32), legacy);
	saveVisit(resumeId, preferences, result(), legacy);
	saveVisit('b'.repeat(32), preferences, undefined, legacy);
	saveTailorReference({ resumeId, jobKey: '1', artifactId: 'c'.repeat(32), downloadPending: true, updatedAt: 1 }, legacy);
	rememberOwnedResumes([owned()], scoped, legacy);
	expect(readHistory(scoped).entries.map((entry) => entry.id)).toEqual([resumeId]);
	expect(readHistory(scoped).entries[0].snapshot?.items).toHaveLength(1);
	expect(readTailorHistory(scoped)[0].downloadPending).toBe(false);
	expect(readHistory(legacy).entries).toHaveLength(2);
});

test('a complete server listing removes deleted CVs but a partial page does not discard older saved CVs', () => {
	const storage = new MemoryStorage();
	saveVisit(resumeId, preferences, result(), storage);
	saveTailorReference({ resumeId, jobKey: '1', artifactId: 'c'.repeat(32), downloadPending: true, updatedAt: 1 }, storage);
	rememberOwnedResumes([], storage, undefined, false);
	expect(readHistory(storage).entries).toHaveLength(1);
	rememberOwnedResumes([], storage, undefined, true);
	expect(readHistory(storage).entries).toEqual([]);
	expect(readTailorHistory(storage)).toEqual([]);
});

test('agent setup uses the current origin and credential references, with no token in the URL', () => {
	expect(agentSetup('codex', 'http://localhost:5174').text).toContain('--url http://localhost:5174/mcp --bearer-token-env-var AVICV_TOKEN');
	expect(agentSetup('claude', 'https://avicv.com').text).toContain("--header 'Authorization: Bearer ${AVICV_TOKEN}'");
	expect(JSON.parse(agentSetup('cursor', 'https://avicv.com').text).mcpServers.avicv.headers.Authorization).toBe('Bearer ${env:AVICV_TOKEN}');
	expect(JSON.parse(agentSetup('vscode', 'https://avicv.com').text).inputs[0].password).toBe(true);
});
