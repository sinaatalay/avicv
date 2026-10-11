<script lang="ts">
	import { resolve } from '$app/paths';
	import Copy from '@lucide/svelte/icons/copy';
	import Check from '@lucide/svelte/icons/check';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { loadGoodMatchLinks, NoApplicationLinks } from './good-match-links.ts';

	let { resumeId, disabled = false }: { resumeId: string; disabled?: boolean } = $props();
	let busy = $state(false);
	let copied = $state(0);
	let message = $state('');
	let manualLinks = $state('');
	let controller: AbortController | undefined;
	const label = $derived(busy ? 'Copying…' : copied ? `Copied ${copied} ${copied === 1 ? 'link' : 'links'}` : 'Copy all links');

	$effect(() => () => controller?.abort());
	$effect(() => {
		if (!copied) return;
		const timer = setTimeout(() => copied = 0, 2500);
		return () => clearTimeout(timer);
	});

	async function copyAll() {
		if (disabled || busy) return;
		busy = true; copied = 0; message = ''; manualLinks = '';
		controller = new AbortController();
		const { signal } = controller;
		const links = loadGoodMatchLinks(resolve('/resumes/[id]/good-match-links', { id: resumeId }), signal);
		const text = links.then((values) => values.join('\n'));
		// Register the clipboard write during the click, before fetching finishes.
		// Promise-backed ClipboardItem data preserves user activation in Safari.
		let write: Promise<boolean>;
		try {
			if (navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
				const contents = text.then((value) => new Blob([value], { type: 'text/plain' }));
				// A denied/synchronous clipboard request may never consume its data.
				void contents.catch(() => {});
				write = navigator.clipboard.write([new ClipboardItem({ 'text/plain': contents })]).then(() => true, () => false);
			} else {
				write = text.then((value) => navigator.clipboard.writeText(value)).then(() => true, () => false);
			}
		} catch { write = Promise.resolve(false); }
		try {
			const values = await links;
			const success = await write;
			if (signal.aborted) return;
			if (success) copied = values.length;
			else manualLinks = values.join('\n');
		} catch (cause) {
			if (!signal.aborted) message = cause instanceof NoApplicationLinks
				? 'No application links available yet.' : 'Couldn’t load your links. Try again.';
		} finally { if (!signal.aborted) busy = false; }
	}
</script>

<div class="copy-all">
	<button class="copy-all-button" disabled={disabled || busy} onclick={() => void copyAll()} aria-label={copied ? label : 'Copy all good match links'}>
		{#if busy}<LoaderCircle size={15} class="spinner" aria-hidden="true" />{:else if copied}<Check size={15} aria-hidden="true" />{:else}<Copy size={15} aria-hidden="true" />{/if}
		<span>{label}</span>
	</button>
	{#if message}<p role="status">{message}</p>{/if}
	{#if manualLinks}
		<label>Select and copy the links.
			<textarea readonly value={manualLinks} rows="4" aria-label="Good match application links" onfocus={(event) => event.currentTarget.select()}></textarea>
		</label>
	{/if}
	<span class="sr-only" role="status">{copied ? label : ''}</span>
</div>

<style>
	.copy-all { margin-left: auto; max-width: 100%; text-align: right; }
	button { display: inline-flex; align-items: center; gap: 0.45rem; padding: 0.4rem 0.65rem; border: 1px solid var(--color-border); border-radius: 0.4rem; background: transparent; color: var(--color-text); cursor: pointer; font-size: 0.8rem; font-weight: 550; white-space: nowrap; }
	button:hover:not(:disabled) { background: var(--color-surface); }
	button:disabled { opacity: 0.5; cursor: default; }
	button:focus-visible, textarea:focus-visible { outline: 2px solid var(--color-text); outline-offset: 3px; }
	:global(.copy-all .spinner) { animation: spin 1s linear infinite; }
	@keyframes spin { to { transform: rotate(360deg); } }
	@media (prefers-reduced-motion: reduce) { :global(.copy-all .spinner) { animation: none; } }
	p, label { display: block; margin: 0.5rem 0 0; font-size: 0.8rem; color: var(--color-muted); }
	textarea { display: block; width: min(26rem, 100%); min-width: 0; margin-top: 0.35rem; padding: 0.5rem; border: 1px solid var(--color-border); border-radius: 0.35rem; background: var(--color-bg); color: var(--color-text); font-size: 0.8rem; resize: vertical; text-align: left; }
	.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
