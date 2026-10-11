<script lang="ts">
	import { uploadResume } from './resumes.remote.ts';
	import { resumeUpload } from './schema.ts';

	let dragging = $state(false);

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

<style>
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
