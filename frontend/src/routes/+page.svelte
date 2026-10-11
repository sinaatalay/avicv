<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { clearHistory, forgetResume, readHistory, type HistoryEntry } from '#lib/resume-history.ts';
	import { uploadResume } from './resumes.remote.ts';
	import { resumeUpload } from './schema.ts';

	let dragging = $state(false);
	let history = $state<HistoryEntry[]>([]);
	let storageWarning = $state<string | null>(null);
	function loadHistory() { const saved = readHistory(); history = saved.entries; storageWarning = saved.warning; }
	onMount(() => {
		loadHistory();
		window.addEventListener('storage', loadHistory);
		return () => window.removeEventListener('storage', loadHistory);
	});
	function forget(id?: string) {
		const saved = id ? forgetResume(id) : clearHistory();
		history = saved.entries; storageWarning = saved.warning;
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
	<title>Speedhack</title>
	<meta name="description" content="Drop your résumé and see which open jobs fit you." />
</svelte:head>

<h1>Find the jobs that fit your résumé</h1>

<p class="lead">
	Drop your résumé as a PDF. We read it, compare it with thousands of open roles in the US and
	Canada, and rank the ones you'd fit.
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
			<strong>Drop your résumé here</strong>
			<span>or click to choose a PDF, up to 10 MB</span>
		{/if}
	</label>

	<noscript><button>Upload</button></noscript>
</form>

{#each uploadResume.fields.resume.issues() as issue (issue.message)}
	<p class="error">{issue.message}</p>
{/each}

{#if storageWarning}<p class="error" role="status">{storageWarning}</p>{/if}
{#if history.length}
	<section class="history" aria-label="Recent résumés">
		<h2>Your saved runs</h2>
		<p>Result links, filters and a preview are saved in this browser. Your full results are stored on the server.</p>
		<ul>{#each history as entry (entry.id)}
			<li><div><a href={resolve('/resumes/[id]', { id: entry.id })}>Résumé from {new Date(entry.updatedAt).toLocaleString()}</a>
				{#if entry.snapshot}<span>{entry.snapshot.status.replaceAll('_', ' ')} · {entry.snapshot.progress.succeeded.toLocaleString()} roles scored</span>{/if}</div>
				<button class="forget" onclick={() => forget(entry.id)} aria-label={`Forget résumé from ${new Date(entry.updatedAt).toLocaleString()} in this browser`}>Forget</button></li>
		{/each}</ul>
		<button onclick={() => forget()}>Clear browser history</button>
		<p class="small">This clears local previews and links. Open a run to delete its server data.</p>
	</section>
{/if}

<style>
	.history { margin-top: 2.5rem; }
	.history p, .history span { color: var(--color-muted); }
	.history ul { list-style: none; padding: 0; }
	.history li { display: flex; gap: 1rem; justify-content: space-between; align-items: center; padding-block: 0.75rem; border-bottom: 1px solid var(--color-border); }
	.history span { display: block; font-size: 0.85rem; }
	.forget { background: none; border: 0; text-decoration: underline; cursor: pointer; color: var(--color-muted); }
	.small { font-size: 0.85rem; }
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
