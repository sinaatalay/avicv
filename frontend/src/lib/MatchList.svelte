<script lang="ts">
	import type { Match } from './contracts.ts';
	let { matches, locationKnown = false }: { matches: Match[]; locationKnown?: boolean } = $props();
	const percent = (value: number) => `${Math.round(value * 100)}%`;
	function salary(match: Match) {
		if (match.salary_min === null || !match.salary_currency) return null;
		try {
			const format = new Intl.NumberFormat('en', { style: 'currency', currency: match.salary_currency, notation: 'compact', maximumFractionDigits: 0 });
			const range = match.salary_max && match.salary_max !== match.salary_min ? `${format.format(match.salary_min)}–${format.format(match.salary_max)}` : format.format(match.salary_min);
			return match.salary_interval ? `${range} / ${match.salary_interval}` : range;
		} catch { return null; }
	}
	function sourceVerification(match: Match) {
		const date = match.source_verified_at ? new Date(match.source_verified_at) : null;
		return date && !Number.isNaN(date.getTime())
			? `Source last verified ${date.toLocaleString()}. Check the original posting for availability.`
			: 'A recent source verification is unavailable. Check the original posting for availability.';
	}
	function safeUrl(url: string | null) { return url && /^https?:\/\//.test(url) ? url : null; }
</script>

<ol class="matches">
	{#each matches as match (match.job_key)}
		{const pay = $derived(salary(match))}
		{const url = $derived(safeUrl(match.url))}
		<li>
			<span class="score" title="Weighted model match score; not an interview probability">{percent(match.score)}</span>
			<div>
				<h2>{#if url}<a href={url} target="_blank" rel="noopener noreferrer">{match.title}</a>{:else}{match.title}{/if}</h2>
				<p>{match.company}<span class="muted">{match.locations.length ? ` · ${match.locations.slice(0, 3).join(' · ')}` : ''}</span>
					{#if match.locations.length > 3}<span class="muted"> +{match.locations.length - 3} more</span>{/if}</p>
				<p class="tags">
					{#if match.workplace_type}<span>{match.workplace_type}</span>{/if}
					{#if pay}<span>{pay}</span>{/if}
					{#if match.seniority === 'below'}<span>A step up for you</span>{/if}
					{#if match.seniority === 'above'}<span>Below your level</span>{/if}
					{#if locationKnown && match.location !== null && match.location < 0.5}<span>May need relocation</span>{/if}
					{#if match.source_stale}<span title={sourceVerification(match)}>Source not recently verified</span>{/if}
					{#if match.catalog_status !== 'open'}<span>{match.catalog_status === 'changed' ? 'Posting changed since scoring' : match.catalog_status === 'closed' ? 'Posting closed' : 'Posting needs verification'}</span>{/if}
				</p>
				<p class="muted small">Model assessment: interview fit {percent(match.interview)} · requirements match {percent(match.must_haves)}</p>
			</div>
		</li>
	{/each}
</ol>
<style>
	.matches { padding: 0; list-style: none; }
	.matches > li { display: flex; gap: 1rem; padding-block: 1rem; border-bottom: 1px solid var(--color-border); }
	.score { flex: none; width: 3.5rem; font-size: 1.25rem; font-weight: 600; color: var(--color-accent); font-variant-numeric: tabular-nums; }
	h2 { margin: 0; font-size: 1.05rem; } h2 a { color: inherit; }
	.matches p { margin: 0.2rem 0 0; }
	.tags { display: flex; flex-wrap: wrap; gap: 0.4rem; }
	.tags span { padding: 0.05rem 0.5rem; border-radius: 999px; font-size: 0.8rem; background-color: var(--color-surface); }
	.muted { color: var(--color-muted); } .small { font-size: 0.85rem; }
</style>
