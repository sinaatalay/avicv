<script lang="ts">
	import { resolve } from '$app/paths';
	import { goto } from '$app/navigation';
	import { untrack } from 'svelte';
	import * as v from 'valibot';
	import type { JobTab, ResumeResults } from '#lib/contracts.ts';
	import MatchList from '#lib/MatchList.svelte';
	import CopyGoodMatchLinks from '#lib/CopyGoodMatchLinks.svelte';
	import TimeSavings from '#lib/TimeSavings.svelte';
	import { timeSavingsSchema, type TimeSavings as TimeSavingsValue } from '#lib/time-savings.ts';
	import { createTailorManager, type TailorStates } from '#lib/tailored-cvs.ts';
	import { cachedMatches, forgetResume, readHistory, saveVisit, type HistoryEntry, type ViewPreferences } from '#lib/resume-history.ts';
	import { deleteResume, rematchResume, retryResume, watchResume } from '../../resumes.remote.ts';
	import type { PageProps } from './$types';

	let { params }: PageProps = $props();
	let category = $state<JobTab>('good');
	let categoryChosen = $state(false);
	let limits = $state({ good: 20, other: 20 });
	let restoredId = $state('');
	let cached = $state<HistoryEntry>();
	let lastView = $state<{ category: JobTab; results: ResumeResults }>();
	let storageWarning = $state<string | null>(null);
	let busy = $state(false);
	let actionError = $state('');
	let confirmDelete = $state(false);
	let tailoring = $state<TailorStates>({});
	let savings = $state<{ resumeId: string; value: TimeSavingsValue } | null>(null);
	let tailorManager: ReturnType<typeof createTailorManager> | undefined;
	const result = $derived(watchResume({ id: params.id, nearby: false, category, limit: limits[category] }));
	const data = $derived(result.current);
	const previous = $derived(lastView?.results.resume.id === params.id ? lastView : undefined);
	const activeCache = $derived(cached?.id === params.id ? cached : undefined);
	const current = $derived(data ?? (previous?.category === category ? previous.results : undefined));
	const r = $derived(data?.resume ?? previous?.results.resume);
	const terminal = $derived(r ? ['done', 'partial', 'failed', 'waiting_for_jobs'].includes(r.status) : false);
	const goodCount = $derived(current?.matches.good_total ?? activeCache?.snapshot?.goodTotal);
	const otherCount = $derived(current?.matches.other_total ?? activeCache?.snapshot?.otherTotal);
	const shown = $derived(current?.matches.items ?? cachedMatches(activeCache, category));
	const activeTotal = $derived(category === 'good' ? goodCount : otherCount);
	const activeSavings = $derived(savings?.resumeId === params.id ? savings.value : null);
	const completedTailoring = $derived(Object.values(tailoring)
		.filter((state) => state.artifact?.status === 'ready')
		.map((state) => state.artifact!.id).sort().join('|'));
	$effect(() => {
		const id = params.id;
		// Refresh authoritative totals as work completes, without counting browser events.
		void r?.progress.succeeded;
		void r?.status;
		void completedTailoring;
		const controller = new AbortController();
		const { signal } = controller;
		void fetch(resolve('/resumes/[id]/time-savings', { id }), { signal, cache: 'no-store' })
			.then(async (response) => {
				if (!response.ok) throw new Error('Estimate unavailable');
				const value = v.parse(timeSavingsSchema, await response.json());
				if (!signal.aborted) savings = { resumeId: id, value };
			})
			.catch(() => { if (!signal.aborted) savings = null; });
		return () => controller.abort();
	});
	$effect(() => {
		const id = params.id;
		tailoring = {};
		const manager = createTailorManager(id, (states) => tailoring = states, { resumePath: resolve('/resumes/[id]', { id }) });
		tailorManager = manager;
		manager.restore();
		return () => manager.dispose();
	});

	function preferences(): ViewPreferences { return { category, categoryChosen, limits: { ...limits } }; }
	function remember(results?: ResumeResults) {
		const saved = saveVisit(params.id, preferences(), results);
		storageWarning = saved.warning;
		cached = saved.entries.find((entry) => entry.id === params.id);
	}

	$effect(() => {
		const id = params.id;
		if (id === untrack(() => restoredId)) return;
		const saved = readHistory();
		cached = saved.entries.find((entry) => entry.id === id);
		category = cached?.category === 'other' ? 'other' : 'good';
		categoryChosen = cached?.categoryChosen ?? false;
		limits = cached?.limits ?? { good: 20, other: 20 };
		lastView = undefined;
		storageWarning = saved.warning;
		confirmDelete = false;
		restoredId = id;
		untrack(() => remember());
	});
	$effect(() => {
		if (restoredId !== params.id || !data) return;
		lastView = { category, results: data };
		untrack(() => remember(data));
		if (!categoryChosen && category === 'good' && ['done', 'partial'].includes(data.resume.status)
			&& data.matches.good_total === 0 && data.matches.other_total > 0) {
			category = 'other';
			untrack(() => remember());
		}
	});

	function chooseCategory(next: JobTab) {
		category = next;
		categoryChosen = true;
		remember();
	}
	function moveTab(event: KeyboardEvent) {
		if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
		event.preventDefault();
		const next = event.key === 'Home' ? 'good' : event.key === 'End' ? 'other' : category === 'good' ? 'other' : 'good';
		chooseCategory(next);
		document.getElementById(`${next}-tab`)?.focus();
	}
	function loadMore() {
		limits[category] += 20;
		remember();
	}
	async function run(action: 'retry' | 'rematch' | 'delete') {
		busy = true;
		actionError = '';
		try {
			if (action === 'delete') {
				await deleteResume(params.id);
				forgetResume(params.id);
				await goto(resolve('/'));
				return;
			}
			if (action === 'retry') await retryResume(params.id);
			else await rematchResume({ id: params.id });
			await result.reconnect();
		} catch { actionError = 'Something went wrong. Please try again.'; }
		finally { busy = false; }
	}
</script>

<svelte:head><title>Your job matches · Speedhack</title></svelte:head>

<div class="page-heading">
	<h1>Your job matches</h1>
	<p class="cv-summary"><span>{r?.filename ?? r?.cv?.name ?? 'Your saved CV'}</span><a href={resolve('/')}>Change CV</a></p>
</div>

<TimeSavings value={activeSavings} />

{#if storageWarning}<p class="notice" role="status">{storageWarning}</p>{/if}
{#if result.error}
	<div class="notice" role="alert"><p>We couldn't load your latest jobs.{shown.length ? ' Your saved jobs are below.' : ''}</p><button onclick={() => void result.reconnect()}>Try again</button></div>
{:else if r && !terminal}
	<p class="status" aria-live="polite">{r.status === 'parsing' ? 'Reading your CV…' : 'Finding jobs for you…'}</p>
{:else if r?.status === 'waiting_for_jobs'}
	<p class="status" aria-live="polite">New jobs are on their way. We'll keep looking for you.</p>
{:else if r?.status === 'failed' || r?.status === 'partial'}
	<div class="notice" role="status"><p>{r.status === 'partial' ? 'Some jobs could not be checked.' : 'We couldn’t finish your search.'}</p><button disabled={busy} onclick={() => void run('retry')}>Try again</button></div>
{/if}

<div class="tabs" role="tablist" aria-label="Job matches">
	<button id="good-tab" role="tab" tabindex={category === 'good' ? 0 : -1} onkeydown={moveTab} aria-selected={category === 'good'} aria-controls="job-results" class:active={category === 'good'} onclick={() => chooseCategory('good')}>Good matches <span>{goodCount === undefined ? '—' : goodCount.toLocaleString()}</span></button>
	<button id="other-tab" role="tab" tabindex={category === 'other' ? 0 : -1} onkeydown={moveTab} aria-selected={category === 'other'} aria-controls="job-results" class:active={category === 'other'} onclick={() => chooseCategory('other')}>Other jobs <span>{otherCount === undefined ? '—' : otherCount.toLocaleString()}</span></button>
</div>

<div id="job-results" role="tabpanel" tabindex="0" aria-labelledby={category === 'good' ? 'good-tab' : 'other-tab'}>
	<div class="results-caption">
		<p>Best matches first.</p>
		{#if category === 'good'}{#key params.id}<CopyGoodMatchLinks resumeId={params.id} disabled={!goodCount} />{/key}{/if}
		{#if category === 'other' && goodCount === 0}<p>No good matches yet.</p>{/if}
	</div>
	{#if !data && !current && shown.length}<p class="saved-notice">Saved jobs. {result.error ? 'Check back when you’re online.' : 'Checking for updates…'}</p>{/if}
	{#if current?.matches.results_are_previous}<p class="saved-notice">Here are your saved jobs while we look for new matches.</p>{/if}
	{#if shown.length}
		<MatchList matches={shown} {tailoring} tailoringSeconds={activeSavings?.cv_tailoring_seconds} onTailor={(jobKey) => void tailorManager?.request(jobKey)} />
		{#if current?.matches.has_more || (activeTotal !== undefined && shown.length < activeTotal && data)}
			<button class="load-more" disabled={result.loading} onclick={loadMore}>{result.loading ? 'Loading…' : 'Show more jobs'}</button>
		{/if}
	{:else if result.loading && !data}
		<p class="empty" role="status">Loading your jobs…</p>
	{:else if category === 'good'}
		<div class="empty"><h2>No good matches yet.</h2><p>{r && !terminal ? 'We’re still looking. New matches will appear here.' : 'You can explore other jobs or try a different CV.'}</p></div>
	{:else if r && !terminal}
		<p class="empty" role="status">Your jobs will appear here as we find them.</p>
	{:else}
		<div class="empty"><h2>No jobs to show yet.</h2><p>Check back soon or try a different CV.</p></div>
	{/if}
</div>

{#if r}
	<details class="more-options">
		<summary>More options</summary>
		<div class="option-actions">
			{#if terminal && r.cv}<button disabled={busy} onclick={() => void run('rematch')}>Look for new jobs</button>{/if}
			{#if confirmDelete}
				<p>Remove this CV and its saved jobs?</p>
				<button disabled={busy} onclick={() => void run('delete')}>Remove CV</button>
				<button disabled={busy} onclick={() => confirmDelete = false}>Cancel</button>
			{:else}<button onclick={() => confirmDelete = true}>Remove this CV</button>{/if}
		</div>
	</details>
{/if}
{#if actionError}<p class="notice" role="alert">{actionError}</p>{/if}

<style>
	h1 { margin: 0; font-size: clamp(1.8rem, 5vw, 2.4rem); line-height: 1.2; letter-spacing: -0.04em; }
	.page-heading { margin-bottom: 2.3rem; }
	.cv-summary { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; margin: 0.85rem 0 0; font-size: 0.85rem; }
	.cv-summary span { color: var(--color-muted); overflow-wrap: anywhere; }
	.cv-summary a { flex: none; font-weight: 500; }
	.status { color: var(--color-muted); margin-block: 0 1.5rem; }
	.tabs { display: flex; gap: 1.8rem; border-bottom: 1px solid var(--color-border); }
	.tabs button { display: flex; align-items: center; gap: 0.6rem; padding: 0 0 0.9rem; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; background: none; color: var(--color-muted); cursor: pointer; font-size: 0.95rem; font-weight: 600; }
	.tabs button.active { color: var(--color-text); border-bottom-color: var(--color-accent); }
	.tabs span { padding: 0.1rem 0.45rem; border-radius: 0.35rem; background: var(--color-surface); font-size: 0.78rem; font-variant-numeric: tabular-nums; }
	.results-caption { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; margin-top: 1rem; color: var(--color-muted); font-size: 0.8rem; }
	.results-caption p { margin: 0; }
	.notice { display: flex; justify-content: space-between; align-items: center; gap: 1rem; padding: 0.85rem 1rem; margin-block: 1rem; border-radius: 0.5rem; background: var(--color-surface); font-size: 0.9rem; }
	.notice p { margin: 0; }
	.notice button { flex: none; }
	.saved-notice { color: var(--color-muted); font-size: 0.85rem; }
	.empty { margin: 0; padding: 3rem 1rem; text-align: center; color: var(--color-muted); }
	.empty h2 { margin: 0 0 0.5rem; color: var(--color-text); font-size: 1.1rem; }
	.empty p { margin: 0; font-size: 0.9rem; }
	.load-more { display: block; width: 100%; padding: 0.8rem; border: 1px solid var(--color-border); border-radius: 0.5rem; background: none; color: var(--color-text); cursor: pointer; font-size: 0.9rem; }
	.load-more:hover { background: var(--color-surface); }
	.more-options { margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid var(--color-border); color: var(--color-muted); font-size: 0.8rem; }
	.more-options summary { width: fit-content; cursor: pointer; }
	.option-actions { display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; margin-block: 1rem; }
	.option-actions p { flex-basis: 100%; margin: 0; }
	.option-actions button, .notice button { border: 0; padding: 0; background: none; color: var(--color-accent); text-decoration: underline; cursor: pointer; font-size: inherit; }
	button:disabled { opacity: 0.6; cursor: wait; }
</style>
