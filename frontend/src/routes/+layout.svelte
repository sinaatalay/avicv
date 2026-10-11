<script lang="ts">
	import { resolve } from '$app/paths';
	import favicon from '#lib/assets/favicon.svg';
	import type { LayoutProps } from './$types';
	import './layout.css';

	let { children }: LayoutProps = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<header>
	<a class="brand" href={resolve('/')}>
		<img src={favicon} alt="" width="22" height="26" />
		Speedhack
	</a>
</header>

<main>
	{@render children()}
</main>

<style>
	header,
	main {
		width: 100%;
		max-width: var(--content-width);
		margin-inline: auto;
		padding-inline: 1.5rem;
	}

	header {
		padding-block: 1.25rem;
		border-bottom: 1px solid var(--color-border);
	}

	.brand {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		font-weight: 600;
		color: inherit;
		text-decoration: none;
	}

	main {
		padding-block: 2rem;
	}
</style>
