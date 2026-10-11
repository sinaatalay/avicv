<script lang="ts">
	let count = $state(0);
	let doubled = $derived(count * 2);
</script>

<div class="counter">
	<button type="button" aria-label="Decrease the count" onclick={() => (count -= 1)}>−</button>
	<output>{count}</output>
	<button type="button" aria-label="Increase the count" onclick={() => (count += 1)}>+</button>
</div>

<p>{count} doubled is {doubled}</p>

<style>
	.counter {
		display: inline-flex;
		align-items: center;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		overflow: hidden;
	}

	button {
		width: 2.75rem;
		height: 2.75rem;
		border: 0;
		font-size: 1.25rem;
		color: inherit;
		background-color: var(--color-surface);
		cursor: pointer;
	}

	button:hover {
		color: var(--color-accent);
	}

	output {
		min-width: 4rem;
		font-variant-numeric: tabular-nums;
		font-size: 1.25rem;
		text-align: center;
	}

	p {
		color: var(--color-muted);
	}
</style>
