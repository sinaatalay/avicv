<script lang="ts">
	import { resolve } from '$app/paths';
	import type { Match } from '#lib/server/api.ts';
	import { watchResume } from '../../resumes.remote.ts';
	import type { PageProps } from './$types';

	let { params }: PageProps = $props();

	const resume = $derived(watchResume(params.id));
	let nearbyOnly = $state(true);

	const percent = (value: number) => `${Math.round(value * 100)}%`;

	function salary(match: Match) {
		if (match.salary_min === null || !match.salary_currency) return null;
		const format = new Intl.NumberFormat('en', {
			style: 'currency',
			currency: match.salary_currency,
			notation: 'compact',
			maximumFractionDigits: 0
		});
		const range =
			match.salary_max && match.salary_max !== match.salary_min
				? `${format.format(match.salary_min)}–${format.format(match.salary_max)}`
				: format.format(match.salary_min);
		return match.salary_interval ? `${range} / ${match.salary_interval}` : range;
	}

	function fileName(name: string | null) {
		return `${(name ?? 'My').replaceAll(/\s+/g, '_')}_CV.yaml`;
	}
</script>

<svelte:head>
	<title>Your matches · Speedhack</title>
</svelte:head>

<svelte:boundary>
	{const { resume: r, matches } = $derived(await resume)}
	{const shown = $derived(nearbyOnly ? matches.filter((m) => m.location >= 0.5) : matches)}

	{#if r.status === 'failed'}
		<h1>That didn't work</h1>
		<p class="error">{r.error}</p>
		<p><a href={resolve('/')}>Try another PDF</a></p>
	{:else}
		{const name = $derived(r.cv?.name ?? r.preview?.name)}
		<h1>{name ? `Matches for ${name}` : 'Your matches'}</h1>

		<p class="status" aria-live="polite">
			{#if r.status === 'parsing'}
				Reading your résumé…
			{:else if r.status === 'matching' && r.progress.total === 0}
				Finding open roles that fit…
			{:else if r.status === 'matching'}
				Scoring {r.progress.done.toLocaleString()} of {r.progress.total.toLocaleString()} roles…
			{:else if r.progress.total === 0}
				No open roles to compare with yet. The first job sync may still be running.
			{:else}
				Scored the {r.progress.total.toLocaleString()} roles closest to your résumé.
			{/if}
		</p>

		{#if r.status === 'parsing' && r.preview?.sections.length}
			<p class="muted small">Found {r.preview.sections.join(' · ')}…</p>
		{/if}

		{#if r.status !== 'done'}
			<progress
				max={r.progress.total || undefined}
				value={r.status === 'matching' ? r.progress.done : undefined}
			></progress>
		{/if}

		{#if r.cv}
			<details class="cv">
				<summary>
					What we read from {r.filename ?? 'your résumé'}:
					{[r.cv.headline, r.cv.location].filter(Boolean).join(' · ')}
				</summary>
				<ul>
					{#each r.cv.sections as section (section.title)}
						<li><strong>{section.title}</strong>: {section.entries.length} entries</li>
					{/each}
				</ul>
				{#if r.rendercv_yaml}
					<a
						href="data:application/yaml;charset=utf-8,{encodeURIComponent(r.rendercv_yaml)}"
						download={fileName(r.cv.name)}
					>
						Download it as a RenderCV YAML file
					</a>
				{/if}
			</details>
		{/if}

		{#if matches.length > 0}
			<label class="filter">
				<input type="checkbox" bind:checked={nearbyOnly} />
				Only roles I can take {r.cv?.location ? `from ${r.cv.location}` : 'where I live'}
			</label>

			<ol class="matches">
				{#each shown as match (match.job_id)}
					{const pay = $derived(salary(match))}
					<li>
						<span class="score" title="Overall fit">{percent(match.score)}</span>
						<div>
							<h2>
								{#if match.url}
									<a href={match.url} target="_blank" rel="noopener noreferrer">{match.title}</a>
								{:else}
									{match.title}
								{/if}
							</h2>
							<p class="company">
								{match.company}
								<span class="muted">· {match.locations.slice(0, 3).join(' · ')}</span>
								{#if match.locations.length > 3}
									<span class="muted">+{match.locations.length - 3} more</span>
								{/if}
							</p>
							<p class="tags">
								{#if match.workplace_type}<span>{match.workplace_type}</span>{/if}
								{#if pay}<span>{pay}</span>{/if}
								{#if match.seniority === 'below'}<span>A step up for you</span>{/if}
								{#if match.seniority === 'above'}<span>Below your level</span>{/if}
								{#if match.location < 0.5}<span>May need relocation</span>{/if}
							</p>
							<p class="muted small">
								Interview chance {percent(match.interview)} · meets every requirement {percent(
									match.must_haves
								)}
							</p>
						</div>
					</li>
				{:else}
					<li class="muted">
						None of the matches so far are near you. Clear the filter to see them all.
					</li>
				{/each}
			</ol>
		{/if}

		{#if r.status === 'done'}
			<p class="muted small">
				<a href={resolve('/')}>Try another résumé</a> · this run cost ${r.cost_usd.toFixed(3)}
			</p>
		{/if}
	{/if}

	<!-- SvelteKit passes errors through handleError, so this is an App.Error -->
	{#snippet failed(error, reset)}
		<h1>Something went wrong</h1>
		<p class="error">{(error as App.Error).message}</p>
		<button
			onclick={() => {
				void resume.reconnect();
				reset();
			}}
		>
			Try again
		</button>
	{/snippet}
</svelte:boundary>

<style>
	.status {
		color: var(--color-muted);
	}

	progress {
		width: 100%;
		accent-color: var(--color-accent);
	}

	.cv {
		margin-block: 1.5rem;
		padding: 0.75rem 1rem;
		border-radius: 0.5rem;
		background-color: var(--color-surface);
	}

	.cv summary {
		cursor: pointer;
	}

	.cv ul {
		margin-block: 0.75rem;
	}

	.filter {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-block: 1.5rem 0.5rem;
	}

	.matches {
		padding: 0;
		list-style: none;
	}

	.matches > li {
		display: flex;
		gap: 1rem;
		padding-block: 1rem;
		border-bottom: 1px solid var(--color-border);
	}

	.score {
		flex: none;
		width: 3.5rem;
		font-size: 1.25rem;
		font-weight: 600;
		color: var(--color-accent);
		font-variant-numeric: tabular-nums;
	}

	h2 {
		margin: 0;
		font-size: 1.05rem;
	}

	h2 a {
		color: inherit;
	}

	.matches p {
		margin: 0.2rem 0 0;
	}

	.tags {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
	}

	.tags span {
		padding: 0.05rem 0.5rem;
		border-radius: 999px;
		font-size: 0.8rem;
		background-color: var(--color-surface);
	}

	.muted {
		color: var(--color-muted);
	}

	.small {
		font-size: 0.85rem;
	}

	.error {
		color: var(--color-accent);
	}
</style>
