<script lang="ts">
	import { onMount } from 'svelte';
	import * as v from 'valibot';
	import { resolve } from '$app/paths';
	import { clearHistory, forgetResume, readHistory, type HistoryEntry } from '#lib/resume-history.ts';
	import { hideResumes, hiddenResumeIds, ownedResumesSchema, recentResumes, rememberOwnedResumes, workspaceStorage, type OwnedResume, type RecentResume } from '#lib/workspace-history.ts';
	import type { PageProps } from './$types';
	import { uploadResume } from './resumes.remote.ts';
	import { resumeUpload } from './schema.ts';

	let { data }: PageProps = $props();
	const storage = $derived(workspaceStorage(data.cacheId));
	let dragging = $state(false);
	let history = $state<HistoryEntry[]>([]);
	let owned = $state<OwnedResume[]>([]);
	let hidden = $state(new Set<string>());
	let hasMore = $state(false);
	let loadingMore = $state(false);
	let syncError = $state(false);
	let offset = 0;
	const recent = $derived(recentResumes(history, owned, hidden));
	let storageWarning = $state<string | null>(null);
	function loadHistory() { const saved = readHistory(storage); history = saved.entries; hidden = hiddenResumeIds(storage); storageWarning = saved.warning; }
	$effect(() => { void data.cacheId; owned = []; offset = 0; hasMore = false; loadHistory(); });
	onMount(() => {
		const controller = new AbortController();
		let refreshing = false;
		async function refresh() {
			if (document.hidden || refreshing) return;
			const workspaceId = data.workspaceId;
			refreshing = true;
			try {
				const response = await fetch(resolve('/resumes'), { signal: controller.signal, cache: 'no-store' });
				if (!response.ok) throw new Error('CVs unavailable');
				const list = v.parse(ownedResumesSchema, await response.json());
				if (workspaceId !== data.workspaceId) return;
				owned = list.has_more ? [...list.items, ...owned.filter((entry) => !list.items.some((item) => item.id === entry.id))] : list.items;
				if (offset === 0) hasMore = list.has_more;
				rememberOwnedResumes(list.items, storage, undefined, !list.has_more);
				loadHistory(); syncError = false;
			} catch { if (!controller.signal.aborted) syncError = true; }
			finally { refreshing = false; }
		}
		loadHistory();
		void refresh();
		window.addEventListener('storage', loadHistory);
		window.addEventListener('focus', refresh);
		document.addEventListener('visibilitychange', refresh);
		const timer = setInterval(refresh, 15000);
		return () => { controller.abort(); clearInterval(timer); window.removeEventListener('storage', loadHistory); window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', refresh); };
	});
	function forget(id?: string) {
		const hiddenSaved = hideResumes(id ? [id] : recent.map((entry) => entry.id), storage);
		const saved = id ? forgetResume(id, storage) : clearHistory(storage);
		history = saved.entries; storageWarning = saved.warning;
		hidden = hiddenResumeIds(storage);
		if (!hiddenSaved) storageWarning = 'Could not save your list on this device.';
	}
	async function loadMore() {
		loadingMore = true;
		try {
			const response = await fetch(`${resolve('/resumes')}?offset=${offset + 100}`, { cache: 'no-store' });
			if (!response.ok) throw new Error('CVs unavailable');
			const list = v.parse(ownedResumesSchema, await response.json());
			owned = [...owned, ...list.items.filter((entry) => !owned.some((item) => item.id === entry.id))];
			offset = list.offset; hasMore = list.has_more; syncError = false;
			rememberOwnedResumes(list.items, storage); loadHistory();
		} catch { syncError = true; }
		finally { loadingMore = false; }
	}
	function savedStatus(entry: RecentResume) {
		if (entry.server) {
			const { status, good_total: count } = entry.server;
			if (status === 'done' || status === 'partial') return `${count.toLocaleString()} good ${count === 1 ? 'match' : 'matches'}`;
			return status === 'failed' ? 'Needs another try' : status === 'parsing' ? 'Reading your CV…' : 'Finding jobs…';
		}
		if (!entry.snapshot) return null;
		const { status, goodTotal } = entry.snapshot;
		if (status === 'done' || status === 'partial') return goodTotal === undefined ? null : `${goodTotal.toLocaleString()} good ${goodTotal === 1 ? 'match' : 'matches'}`;
		if (status === 'failed') return 'Needs another try';
		return status === 'parsing' ? 'Reading your CV…' : 'Finding jobs…';
	}

	function drop(event: DragEvent & { currentTarget: HTMLLabelElement }) {
		event.preventDefault();
		dragging = false;

		const input = event.currentTarget.querySelector('input');
		const files = event.dataTransfer?.files;
		if (!input || !files?.length) return;

		input.files = files;
		// The form tracks its fields through `input` events, so announce the new file before submitting
		input.dispatchEvent(new Event('input', { bubbles: true }));
		input.form?.requestSubmit();
	}
</script>

<svelte:head>
	<title>AviCV · Find jobs that fit your CV</title>
	<meta name="description" content="Find jobs that fit your CV and apply directly." />
</svelte:head>

<h1>Find jobs that fit you</h1>

<p class="lead">
	Upload your CV. Find your best matches and apply.
</p>

<form {...uploadResume.preflight(resumeUpload)} enctype="multipart/form-data">
	<label
		class="dropzone"
		class:dragging
		ondragenter={(event) => {
			event.preventDefault();
			dragging = true;
		}}
		ondragover={(event) => event.preventDefault()}
		ondragleave={() => (dragging = false)}
		ondrop={drop}
	>
		<input
			{...uploadResume.fields.resume.as('file')}
			accept="application/pdf"
			onchange={(event) => event.currentTarget.form?.requestSubmit()}
		/>

		{#if uploadResume.pending}
			<strong>Uploading…</strong>
		{:else}
			<strong>Choose a CV</strong>
			<span>or drop it here · PDF, up to 10 MB</span>
		{/if}
	</label>

	<noscript><button>Upload</button></noscript>
</form>

{#each uploadResume.fields.resume.issues() as issue (issue.message)}
	<p class="error">{issue.message}</p>
{/each}

{#if storageWarning}<p class="error" role="status">Your recent CVs couldn't be saved in this browser.</p>{/if}
{#if syncError}<p class="sync-notice" role="status">Your latest CVs couldn’t be loaded. We’ll try again shortly.</p>{/if}
{#if recent.length || hasMore}
	<section class="history" aria-label="Recent CVs">
		<h2>Recent CVs</h2>
		<ul>{#each recent as entry (entry.id)}
			<li><div><a href={resolve('/resumes/[id]', { id: entry.id })}>{entry.server?.filename ?? `CV from ${new Date(entry.updatedAt).toLocaleString()}`}</a>
				{#if savedStatus(entry)}<span>{savedStatus(entry)}</span>{/if}</div>
				<button class="forget" onclick={() => forget(entry.id)} aria-label={`Remove CV from ${new Date(entry.updatedAt).toLocaleString()} from this list`}>Remove</button></li>
		{/each}</ul>
		<button class="forget clear" onclick={() => forget()}>Clear list</button>
		{#if hasMore}<button class="forget clear" disabled={loadingMore} onclick={() => void loadMore()}>Show more CVs</button>{/if}
		<p class="sync-notice">Removing a CV from this list hides it in this browser. Connected agents can still access it.</p>
	</section>
{/if}

<style>
	.history { margin-top: 2.5rem; }
	.sync-notice { font-size: 0.8rem; color: var(--color-muted); }
	.history span { color: var(--color-muted); }
	.history li > div { min-width: 0; }
	.history a { overflow-wrap: anywhere; }
	.history ul { list-style: none; padding: 0; }
	.history li { display: flex; gap: 1rem; justify-content: space-between; align-items: center; padding-block: 0.75rem; border-bottom: 1px solid var(--color-border); }
	.history span { display: block; font-size: 0.85rem; }
	.forget { flex: none; background: none; border: 0; text-decoration: underline; cursor: pointer; color: var(--color-muted); }
	.clear { padding: 0; margin: 0.5rem 1rem 0 0; font-size: 0.85rem; }
	h1 { font-size: clamp(1.8rem, 5vw, 2.4rem); line-height: 1.2; letter-spacing: -0.035em; margin-bottom: 0.75rem; }
	.lead {
		max-width: 38rem;
		color: var(--color-muted);
	}

	.dropzone {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.35rem;
		margin-top: 2rem;
		padding: 3.5rem 1.5rem;
		border: 2px dashed var(--color-border);
		border-radius: 0.75rem;
		text-align: center;
		cursor: pointer;
		transition:
			border-color 0.15s,
			background-color 0.15s;
	}

	.dropzone:hover,
	.dropzone.dragging,
	.dropzone:focus-within {
		border-color: var(--color-accent);
		background-color: var(--color-surface);
	}

	.dropzone input {
		/* Visually hidden, but still focusable and announced as a file input */
		position: absolute;
		width: 1px;
		height: 1px;
		opacity: 0;
	}

	.dropzone strong {
		font-size: 1.15rem;
	}

	.dropzone span {
		color: var(--color-muted);
		font-size: 0.9rem;
	}

	noscript button {
		margin-top: 1rem;
	}

	.error {
		color: var(--color-accent);
	}
</style>
