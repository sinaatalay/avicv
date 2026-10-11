<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import favicon from '#lib/assets/favicon.svg';
	import type { LayoutProps } from './$types';
	import './layout.css';

	let { children }: LayoutProps = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="app">
	<header>
		<a class="brand" href={resolve('/')}>
			<img src={favicon} alt="" width="22" height="26" />
			Speedhack
		</a>

		<nav aria-label="Main">
			<a href={resolve('/')} aria-current={page.route.id === '/' ? 'page' : undefined}>Home</a>
			<a href={resolve('/items')} aria-current={page.route.id === '/items' ? 'page' : undefined}>
				Items
			</a>
			<a href={resolve('/about')} aria-current={page.route.id === '/about' ? 'page' : undefined}>
				About
			</a>
		</nav>
	</header>

	<main>
		{@render children()}
	</main>

	<footer>
		Built with <a href="https://svelte.dev/docs/kit">SvelteKit</a>
	</footer>
</div>

<style>
	.app {
		display: flex;
		flex-direction: column;
		min-height: 100dvh;
	}

	header,
	main,
	footer {
		width: 100%;
		max-width: var(--content-width);
		margin-inline: auto;
		padding-inline: 1.5rem;
	}

	header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		padding-block: 1.25rem;
		border-bottom: 1px solid var(--color-border);
	}

	.brand {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-weight: 600;
		color: inherit;
		text-decoration: none;
	}

	nav {
		display: flex;
		gap: 1.25rem;
	}

	nav a {
		color: var(--color-muted);
		text-decoration: none;
	}

	nav a:hover,
	nav a[aria-current='page'] {
		color: var(--color-text);
	}

	nav a[aria-current='page'] {
		text-decoration: underline;
		text-decoration-color: var(--color-accent);
		text-decoration-thickness: 2px;
		text-underline-offset: 0.4em;
	}

	main {
		flex: 1;
		padding-block: 2rem;
	}

	footer {
		padding-block: 1.5rem;
		border-top: 1px solid var(--color-border);
		font-size: 0.875rem;
		color: var(--color-muted);
	}
</style>
