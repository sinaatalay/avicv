<script lang="ts">
	import Clock from '@lucide/svelte/icons/clock';
	import { formatEstimateRate, formatSavedTime, type TimeSavings } from './time-savings.ts';
	let { value }: { value: TimeSavings | null } = $props();
</script>

{#if value && value.estimated_seconds > 0}
	<section class="time-savings" aria-label="Estimated time saved">
		<div class="savings-summary">
			<span class="clock"><Clock size={23} strokeWidth={1.7} aria-hidden="true" /></span>
			<div>
				<p class="eyebrow">Estimated time saved</p>
				<p class="time-savings-value">{value.estimated_seconds < 60 ? '' : '~'}{formatSavedTime(value.estimated_seconds)} saved</p>
				<p class="savings-work">{value.jobs_reviewed.toLocaleString()} {value.jobs_reviewed === 1 ? 'job' : 'jobs'} checked{#if value.cvs_tailored > 0}{' · '}{value.cvs_tailored.toLocaleString()} {value.cvs_tailored === 1 ? 'CV' : 'CVs'} tailored{/if}</p>
			</div>
		</div>
		<details>
			<summary>How we estimate</summary>
			<p>{formatEstimateRate(value.job_review_seconds)} per job checked + {formatEstimateRate(value.cv_tailoring_seconds)} per tailored CV. These estimate manual effort; actual times vary. Each job and tailored CV counts once. Downloads and retries add no time.</p>
		</details>
	</section>
{/if}

<style>
	.time-savings { margin: -0.6rem 0 2rem; padding: 1.1rem 1.25rem; border: 1px solid color-mix(in srgb, var(--color-accent) 20%, var(--color-border)); border-radius: 0.75rem; background: color-mix(in srgb, var(--color-accent) 5%, var(--color-bg)); }
	.savings-summary { display: flex; align-items: center; gap: 0.9rem; }
	.clock { display: flex; flex: none; align-items: center; justify-content: center; width: 2.9rem; height: 2.9rem; border-radius: 50%; color: var(--color-accent); background: color-mix(in srgb, var(--color-accent) 10%, var(--color-bg)); }
	p { margin: 0; }
	.eyebrow { color: var(--color-muted); font-size: 0.76rem; font-weight: 500; }
	.time-savings-value { margin-block: 0.1rem; font-size: clamp(1.35rem, 4vw, 1.6rem); font-weight: 650; line-height: 1.3; letter-spacing: -0.025em; font-variant-numeric: tabular-nums; }
	.savings-work { color: var(--color-muted); font-size: 0.8rem; }
	details { margin-top: 0.55rem; padding-left: 3.8rem; color: var(--color-muted); font-size: 0.72rem; }
	summary { width: fit-content; cursor: pointer; }
	summary:focus-visible { outline: 2px solid var(--color-text); outline-offset: 3px; }
	details p { max-width: 36rem; margin-top: 0.45rem; line-height: 1.6; }
	@media (max-width: 400px) { .time-savings { padding: 1rem; } .savings-summary { gap: 0.7rem; } details { padding-left: 0; } }
</style>
