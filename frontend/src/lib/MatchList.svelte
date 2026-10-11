<script lang="ts">
	import type { Match } from './contracts.ts';
	let { matches }: { matches: Match[] } = $props();

	function salary(match: Match) {
		if (match.salary_min === null || !match.salary_currency) return null;
		try {
			const format = new Intl.NumberFormat('en', {
				style: 'currency', currency: match.salary_currency,
				notation: 'compact', maximumFractionDigits: 0
			});
			const range = match.salary_max && match.salary_max !== match.salary_min
				? `${format.format(match.salary_min)}–${format.format(match.salary_max)}`
				: format.format(match.salary_min);
			return match.salary_interval ? `${range} / ${match.salary_interval}` : range;
		} catch { return null; }
	}
	function locationLabel(match: Match) {
		const locations = [...new Set(match.locations.filter(Boolean))];
		let label = locations.slice(0, 3).join(' · ');
		if (locations.length > 3) label += ` +${locations.length - 3} more`;
		const workplace = match.workplace_type === 'remote' ? 'Remote' : match.workplace_type === 'hybrid' ? 'Hybrid' : null;
		if (workplace && !locations.some((location) => location.toLowerCase().includes(workplace.toLowerCase()))) {
			label += `${label ? ' · ' : ''}${workplace}`;
		}
		return label;
	}
	function applicationUrl(match: Match) {
		return match.catalog_status !== 'closed' && match.url && /^https?:\/\//.test(match.url)
			? match.url : null;
	}
</script>

<ol class="matches">
	{#each matches as match (match.job_key)}
		{const pay = $derived(salary(match))}
		{const url = $derived(applicationUrl(match))}
		{const location = $derived(locationLabel(match))}
		<li class="job-card">
			<div class="job-details">
				<h2>{match.title}</h2>
				{#if match.company}<p class="company">{match.company}</p>{/if}
				{#if location}<p class="location">{location}</p>{/if}
				{#if pay}<p class="salary">{pay}</p>{/if}
				{#if match.catalog_status === 'closed'}
					<p class="availability">Applications closed</p>
				{:else if !url}
					<p class="availability">Application link unavailable</p>
				{:else if match.catalog_status === 'missing'}
					<p class="availability">This listing may no longer be available.</p>
				{/if}
			</div>
			{#if url}
				<a class="apply" href={url} target="_blank" rel="noopener noreferrer"
					aria-label={`Apply for ${match.title}${match.company ? ` at ${match.company}` : ''}`}>Apply <span aria-hidden="true">↗</span></a>
			{/if}
		</li>
	{/each}
</ol>

<style>
	.matches { padding: 0; margin: 1rem 0 1.5rem; list-style: none; display: grid; gap: 0.85rem; }
	.job-card { display: flex; align-items: center; justify-content: space-between; gap: 1.5rem; padding: 1.5rem; border: 1px solid var(--color-border); border-radius: 0.8rem; }
	.job-details { min-width: 0; }
	h2 { margin: 0 0 0.35rem; font-size: 1.08rem; line-height: 1.4; font-weight: 650; }
	p { margin: 0.2rem 0 0; font-size: 0.9rem; }
	.company { font-weight: 500; }
	.location, .availability { color: var(--color-muted); }
	.salary { margin-top: 0.55rem; font-size: 0.85rem; }
	.availability { font-size: 0.8rem; }
	.apply { flex: none; display: inline-flex; align-items: center; gap: 0.6rem; padding: 0.55rem 0.9rem; border-radius: 0.45rem; background: var(--color-accent); color: light-dark(white, var(--color-bg)); font-size: 0.9rem; font-weight: 600; text-decoration: none; }
	.apply:hover { filter: brightness(0.92); }
	.apply:focus-visible { outline: 2px solid var(--color-text); outline-offset: 3px; }
	@media (max-width: 520px) { .job-card { align-items: flex-start; flex-direction: column; gap: 1rem; padding: 1.15rem; } .apply { align-self: flex-start; } }
</style>
