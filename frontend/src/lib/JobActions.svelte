<script lang="ts">
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Copy from '@lucide/svelte/icons/copy';
	import Check from '@lucide/svelte/icons/check';
	import FilePen from '@lucide/svelte/icons/file-pen';
	import Download from '@lucide/svelte/icons/download';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import type { TailorJobState } from './tailored-cvs.ts';
	let { title, company, url, tailoring, onTailor }: {
		title: string; company: string | null; url: string | null;
		tailoring?: TailorJobState; onTailor: () => void;
	} = $props();
	let copied = $state(false);
	let copyFailed = $state(false);
	let copying = $state(false);
	const label = $derived(`${title}${company ? ` at ${company}` : ''}`);
	const isReady = $derived(tailoring?.artifact?.status === 'ready');
	const tailorLabel = $derived(tailoring?.busy ? (isReady ? 'Downloading…' : 'Preparing CV…') : isReady ? 'Download CV' : 'Tailor CV');

	$effect(() => {
		if (!copied) return;
		const timer = setTimeout(() => copied = false, 2500);
		return () => clearTimeout(timer);
	});

	async function copyLink() {
		if (!url || copying) return;
		copying = true; copyFailed = false;
		try {
			if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
			await navigator.clipboard.writeText(url);
			copied = true;
		} catch { copyFailed = true; }
		finally { copying = false; }
	}
</script>

<div class="actions-area">
	<div class="job-actions" aria-label={`Actions for ${label}`}>
		{#if url}
			<a class="apply" href={url} target="_blank" rel="noopener noreferrer" aria-label={`Apply for ${label}`}><ExternalLink size={16} aria-hidden="true" /><span>Apply</span></a>
		{:else}<button class="apply" disabled><ExternalLink size={16} aria-hidden="true" /><span>Apply</span></button>{/if}
		<button disabled={!url || copying} onclick={() => void copyLink()} aria-label={copied ? 'Link copied' : `Copy application link for ${label}`}>
			{#if copied}<Check size={16} aria-hidden="true" />{:else}<Copy size={16} aria-hidden="true" />{/if}<span>{copied ? 'Copied' : 'Copy link'}</span>
		</button>
		<button disabled={tailoring?.busy} onclick={onTailor} aria-label={`${tailorLabel} for ${label}`}>
			{#if tailoring?.busy}<LoaderCircle size={16} class="spinner" aria-hidden="true" />{:else if isReady}<Download size={16} aria-hidden="true" />{:else}<FilePen size={16} aria-hidden="true" />{/if}<span>{tailorLabel}</span>
		</button>
	</div>
	{#if copyFailed}
		<label class="copy-fallback">Select and copy the link.
			<input readonly value={url ?? ''} onfocus={(event) => event.currentTarget.select()} aria-label={`Application link for ${label}`} />
		</label>
	{/if}
	{#if tailoring?.error}<p class="action-message" role="alert">{tailoring.error}</p>{/if}
	{#if tailoring?.storageWarning}<p class="action-message" role="status">Keep this page open until your CV downloads.</p>{/if}
	{#if tailoring?.artifact?.design_source === 'default'}<p class="action-message">Original layout unavailable; using a clean layout.</p>{/if}
	<span class="sr-only" role="status">{copied ? 'Application link copied.' : tailoring?.busy ? tailorLabel : ''}</span>
</div>

<style>
	.actions-area { width: 100%; min-width: 0; }
	.job-actions { display: flex; flex-direction: column; align-items: stretch; gap: 0.45rem; }
	button, .apply { display: inline-flex; justify-content: flex-start; align-items: center; gap: 0.6rem; width: 100%; min-height: 2.6rem; padding: 0.6rem 0.8rem; border: 1px solid var(--color-border); border-radius: 0.45rem; background: transparent; color: var(--color-text); font-size: 0.82rem; font-weight: 550; line-height: 1.4; text-decoration: none; cursor: pointer; white-space: nowrap; }
	.job-actions :global(svg) { flex: none; }
	:global(.job-actions .spinner) { animation: spin 1s linear infinite; }
	@keyframes spin { to { transform: rotate(360deg); } }
	@media (prefers-reduced-motion: reduce) { :global(.job-actions .spinner) { animation: none; } }
	@media (max-width: 520px) { button, .apply { font-size: 0.78rem; gap: 0.45rem; padding-inline: 0.65rem; } }
	.apply { background: var(--color-accent); border-color: var(--color-accent); color: light-dark(white, var(--color-bg)); font-weight: 650; }
	button:hover:not(:disabled) { background: var(--color-surface); }
	.apply:hover { filter: brightness(0.92); }
	button:disabled { opacity: 0.55; cursor: default; }
	button:focus-visible, .apply:focus-visible, input:focus-visible { outline: 2px solid var(--color-text); outline-offset: 3px; }
	.action-message, .copy-fallback { display: block; margin: 0.55rem 0 0; color: var(--color-muted); font-size: 0.78rem; }
	.copy-fallback input { display: block; width: 100%; margin-top: 0.3rem; padding: 0.45rem; border: 1px solid var(--color-border); border-radius: 0.35rem; background: var(--color-bg); color: var(--color-text); font-size: 0.8rem; }
	.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
