<script lang="ts">
	import { resolve } from '$app/paths';
	import favicon from '#lib/assets/favicon.svg';
	import type { LayoutProps } from './$types';
	import './layout.css';

	let { children }: LayoutProps = $props();
</script>

<svelte:head>
	<link rel="icon" type="image/svg+xml" href={favicon} />
</svelte:head>

<div class="app-shell">
	<header>
		<a class="brand" href={resolve('/')} aria-label="AviCV home">
			<img src={favicon} alt="" width="34" height="34" />
			<span>AviCV</span>
		</a>
	</header>

	<main>
		{@render children()}
	</main>

	<footer>
		<div class="footer-links">
			<a class="domain" href={resolve('/')}>avicv.com</a>
			<nav aria-label="Legal">
				<a href={resolve('/privacy')}>Privacy</a>
				<a href={resolve('/terms')}>Terms</a>
			</nav>
		</div>
		<p>An experimental project. Upload your CV at your own risk.</p>
	</footer>
</div>

<style>
	.app-shell {
		display: flex;
		min-height: 100svh;
		flex-direction: column;
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
		padding-block: 1.25rem;
		border-bottom: 1px solid var(--color-border);
	}

	.brand {
		display: inline-flex;
		align-items: center;
		gap: 0.65rem;
		font-size: 1.35rem;
		font-weight: 750;
		letter-spacing: -0.045em;
		color: inherit;
		text-decoration: none;
	}

	main {
		flex: 1;
		padding-block: 2rem;
	}

	footer {
		padding-block: 1.25rem;
		border-top: 1px solid var(--color-border);
		font-size: 0.8rem;
		color: var(--color-muted);
	}

	.footer-links,
	nav {
		display: flex;
		align-items: center;
		gap: 1.25rem;
	}

	.footer-links {
		justify-content: space-between;
	}

	footer a {
		color: inherit;
		text-decoration: none;
	}

	footer a:hover {
		color: var(--color-accent);
		text-decoration: underline;
		text-underline-offset: 0.2em;
	}

	.domain {
		font-weight: 600;
	}

	footer p {
		margin: 0.5rem 0 0;
		font-size: 0.75rem;
	}
</style>
