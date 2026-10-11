<script lang="ts">
	import { resolve } from '$app/paths';
	import { goto } from '$app/navigation';
	import { untrack } from 'svelte';
	import MatchList from '#lib/MatchList.svelte';
	import { cachedMatches, forgetResume, readHistory, saveVisit, type HistoryEntry } from '#lib/resume-history.ts';
	import { deleteResume, rematchResume, retryResume, watchResume } from '../../resumes.remote.ts';
	import type { PageProps } from './$types';

	let { params }: PageProps = $props();
	let nearbyOnly = $state(false);
	let limit = $state(100);
	let restoredId = $state('');
	let cached = $state<HistoryEntry>();
	let storageWarning = $state<string | null>(null);
	let location = $state('');
	let busy = $state(false);
	let actionError = $state('');
	let confirmDelete = $state(false);
	const result = $derived(watchResume({ id: params.id, nearby: nearbyOnly, limit }));
	const data = $derived(result.current);
	const r = $derived(data?.resume);
	const terminal = $derived(r ? ['done', 'partial', 'failed', 'waiting_for_jobs'].includes(r.status) : false);

	$effect(() => {
		const id = params.id;
		if (id === untrack(() => restoredId)) return;
		const saved = readHistory();
		cached = saved.entries.find((entry) => entry.id === id);
		nearbyOnly = cached?.nearbyOnly ?? false;
		limit = cached?.limit ?? 100;
		storageWarning = saved.warning;
		const remembered = saveVisit(id, nearbyOnly, undefined, undefined, limit);
		storageWarning ??= remembered.warning;
		location = '';
		confirmDelete = false;
		restoredId = id;
	});
	$effect(() => {
		if (restoredId !== params.id || !data) return;
		const saved = saveVisit(params.id, nearbyOnly, data, undefined, limit);
		storageWarning = saved.warning;
		cached = saved.entries.find((entry) => entry.id === params.id);
	});

	function changeFilter(event: Event) {
		nearbyOnly = (event.currentTarget as HTMLInputElement).checked;
		limit = 100;
		const saved = saveVisit(params.id, nearbyOnly);
		cached = saved.entries.find((entry) => entry.id === params.id);
		storageWarning = saved.warning;
	}
	async function run(action: 'retry' | 'rematch' | 'location' | 'delete') {
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
			else await rematchResume({ id: params.id, ...(action === 'location' ? { location: location.trim() } : {}) });
			await result.reconnect();
		} catch (cause) { actionError = cause instanceof Error ? cause.message : 'Could not complete this action. Please try again.'; }
		finally { busy = false; }
	}
	function fileName(name: string | null) { return `${(name ?? 'My').replaceAll(/\s+/g, '_')}_CV.yaml`; }
</script>

<svelte:head><title>Your matches · Speedhack</title></svelte:head>

<h1>{r?.cv?.name || r?.preview?.name ? `Matches for ${r.cv?.name ?? r.preview?.name}` : 'Your matches'}</h1>
{#if storageWarning}<p class="notice" role="status">{storageWarning}</p>{/if}
{#if result.error}
	<p class="error" role="alert">{result.error.message} Showing the last received or saved data, which may be out of date. Your result link is saved.</p>
	<button onclick={() => void result.reconnect()}>Reconnect</button>
{/if}

{#if r && data}
	<p class="status" aria-live="polite">
		{#if r.status === 'queued'}Your résumé is queued for processing.
		{:else if r.status === 'parsing'}Reading your résumé…
		{:else if r.status === 'matching'}
			{#if r.progress.total === 0}Finding open roles that fit…
			{:else}Scored {r.progress.succeeded.toLocaleString()} of {r.progress.total.toLocaleString()} shortlisted roles. {r.progress.pending.toLocaleString()} pending{r.progress.failed ? `, ${r.progress.failed.toLocaleString()} failed` : ''}…{/if}
		{:else if r.status === 'waiting_for_jobs'}Waiting for the job catalog. Matching will resume when jobs are available.
		{:else if r.status === 'failed'}This run needs attention. {r.error ?? 'Please retry.'}
		{:else if r.status === 'partial'}Scored {r.progress.succeeded.toLocaleString()} roles; {r.progress.failed.toLocaleString()} could not be scored. You can retry those roles below.
		{:else}Scored {r.progress.succeeded.toLocaleString()} of {r.progress.total.toLocaleString()} shortlisted roles.{/if}
	</p>
	{#if r.status === 'parsing' && r.preview?.sections.length}<p class="muted small">Found {r.preview.sections.join(' · ')}…</p>{/if}
	{#if !terminal}<progress max={r.progress.total || undefined} value={r.status === 'matching' && r.progress.total ? r.progress.attempted : undefined}></progress>{/if}

	{#if r.search}
		<dl class="counts" aria-label="Search coverage">
			<div><dt>Open postings</dt><dd>{r.search.catalog_total.toLocaleString()}</dd></div>
			<div><dt>Eligible postings</dt><dd>{r.search.eligible_total.toLocaleString()}</dd></div>
			<div><dt>Keyword matches</dt><dd>{r.search.matching_postings.toLocaleString()}</dd></div>
			<div><dt>Distinct roles</dt><dd>{r.search.distinct_roles.toLocaleString()}</dd></div>
			<div><dt>Shortlisted</dt><dd>{r.search.shortlisted.toLocaleString()}</dd></div>
			<div><dt>Scored</dt><dd>{r.progress.succeeded.toLocaleString()}</dd></div>
			<div><dt>Failed</dt><dd>{r.progress.failed.toLocaleString()}</dd></div>
			<div><dt>Pending</dt><dd>{r.progress.pending.toLocaleString()}</dd></div>
		</dl>
		<p class="muted small">Keyword retrieval selects up to {r.search.retrieval_limit.toLocaleString()} distinct roles for model scoring from eligible US, Canada and unspecified remote postings. {r.search.missing_descriptions ? `${r.search.missing_descriptions.toLocaleString()} shortlisted postings lack a full description.` : ''}</p>
	{:else if r.progress.total > 0}
		<p class="muted small">This saved run shortlisted {r.progress.total.toLocaleString()} roles. Earlier catalog and retrieval counts were not recorded.</p>
	{/if}

	{#if r.cv}
		<details class="cv">
			<summary>What we read from {r.filename ?? 'your résumé'}: {[r.cv.headline, r.cv.location].filter(Boolean).join(' · ') || 'View extracted sections'}</summary>
			<ul>{#each r.cv.sections as section (section.title)}<li><strong>{section.title}</strong>: {section.entries.length} entries</li>{/each}</ul>
			{#if r.rendercv_yaml}<a href="data:application/yaml;charset=utf-8,{encodeURIComponent(r.rendercv_yaml)}" download={fileName(r.cv.name)}>Download the RenderCV YAML file</a>{/if}
		</details>
		{#if !data.matches.candidate_location_known}
			<p class="notice">This earlier run had no location to assess. All scored roles are shown. Refresh matching to use United States as the default location, or enter a location below.</p>
		{:else if r.location_source === 'default'}
			<p class="notice">Using United States because your résumé did not include a location.</p>
		{/if}
		<details class="location">
			<summary>{r.effective_location ? `Update your location (${r.effective_location})` : 'Set a different location (optional)'}</summary>
			<form onsubmit={(event) => { event.preventDefault(); void run('location'); }}>
				<label for="location">Location to use when assessing roles</label>
				<div class="actions"><input id="location" name="location" bind:value={location} placeholder={r.effective_location ?? 'United States'} minlength="2" maxlength="200" required />
					<button disabled={busy || !terminal}>Save and rescore</button></div>
				<p class="muted small">Updating this location reruns model scoring and adds to this run's cost.</p>
			</form>
		</details>
	{/if}

	{#if data.matches.results_are_previous}<p class="notice">Showing your previous results while the new run prepares scores. The counts above describe the new run.</p>{/if}
	{#if data.matches.unfiltered_total > 0}
		<label class="filter"><input type="checkbox" checked={nearbyOnly && data.matches.candidate_location_known} onchange={changeFilter} disabled={!data.matches.candidate_location_known || result.loading} />
			Only roles likely accessible {r.effective_location ? `from ${r.effective_location}` : 'from my location'}{data.matches.nearby_total !== null ? ` (${data.matches.nearby_total.toLocaleString()})` : ''}</label>
		<p class="muted small" aria-live="polite">Showing {data.matches.items.length.toLocaleString()} of {data.matches.total.toLocaleString()} {nearbyOnly && data.matches.candidate_location_known ? 'location-compatible' : 'scored'} roles{nearbyOnly && data.matches.candidate_location_known ? ` (${data.matches.unfiltered_total.toLocaleString()} scored in total)` : ''}.</p>
		{#if data.matches.closed_total}<p class="muted small">{data.matches.closed_total.toLocaleString()} previously scored postings have closed and are excluded from these results.</p>{/if}
		<p class="muted small">Scores are model assessments, not measured interview probabilities. Check each posting's requirements and location restrictions.</p>
		<MatchList matches={data.matches.items} locationKnown={data.matches.candidate_location_known} />
		{#if !data.matches.items.length}<p>No scored roles match this location filter. Clear it to see all roles.</p>{/if}
		{#if data.matches.has_more}<button disabled={result.loading} onclick={() => limit += 100}>{result.loading ? 'Loading…' : 'Load 100 more roles'}</button>{/if}
	{:else if r.status === 'done'}<p>{r.progress.total ? 'No saved roles are currently available.' : 'No roles were shortlisted from this catalog.'} You can refresh matching after the catalog changes.</p>{/if}

	<div class="actions run-actions">
		{#if r.status === 'partial' || r.status === 'failed'}<button disabled={busy} onclick={() => void run('retry')}>Retry incomplete work</button>{/if}
		{#if terminal && r.cv}<button disabled={busy} onclick={() => void run('rematch')}>Refresh matching</button>{/if}
		<a href={resolve('/')}>Résumé history</a>
	</div>
	<p class="muted small">Estimated run cost ${r.cost_usd.toFixed(3)}. Result links and filters are saved in this browser.</p>
	{#if confirmDelete}
		<p>Delete this résumé and its saved results from the server and this browser?</p>
		<div class="actions"><button disabled={busy} onclick={() => void run('delete')}>Delete résumé and results</button><button disabled={busy} onclick={() => confirmDelete = false}>Cancel</button></div>
	{:else}<button class="text-button" onclick={() => confirmDelete = true}>Delete this résumé…</button>{/if}
{:else}
	<p aria-live="polite">{result.error ? 'Live results are unavailable.' : 'Loading your saved run…'}</p>
	{#if cached?.snapshot}
		<p class="notice">Saved browser preview from {new Date(cached.updatedAt).toLocaleString()}. {cached.snapshot.progress.succeeded.toLocaleString()} roles scored. This preview may be out of date.</p>
		<MatchList matches={cachedMatches(cached)} />
	{/if}
{/if}
{#if actionError}<p class="error" role="alert">{actionError}</p>{/if}

<style>
	.status, .muted { color: var(--color-muted); }
	.small { font-size: 0.85rem; }
	.error { color: var(--color-accent); }
	.notice { padding: 0.75rem 1rem; background: var(--color-surface); border-left: 3px solid var(--color-accent); }
	progress { width: 100%; accent-color: var(--color-accent); }
	.cv { margin-block: 1.5rem; padding: 0.75rem 1rem; border-radius: 0.5rem; background-color: var(--color-surface); }
	summary { cursor: pointer; }
	.cv ul { margin-block: 0.75rem; }
	.location { margin-block: 1rem; }
	.location form { margin-block: 1rem; }
	.filter { display: flex; align-items: center; gap: 0.5rem; margin-block: 1.5rem 0.5rem; }
	.counts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.75rem; padding: 1rem; background: var(--color-surface); border-radius: 0.5rem; }
	dt { color: var(--color-muted); font-size: 0.8rem; } dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }
	.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; margin-block: 0.5rem; }
	.run-actions { margin-top: 1.5rem; }
	input[name='location'] { min-width: 0; flex: 1; padding: 0.4rem 0.5rem; }
	.text-button { padding: 0; background: none; border: 0; color: var(--color-muted); text-decoration: underline; cursor: pointer; }
	@media (max-width: 520px) { .counts { grid-template-columns: repeat(2, 1fr); } }
</style>
