<script lang="ts">
	import { resolve } from '$app/paths';
	import type { ErrorProps } from './$types';

	let { error }: ErrorProps = $props();
</script>

<svelte:head>
	<title>{error.status} · AviCV</title>
</svelte:head>

<h1>{error.status}</h1>

<p>{error.message}</p>

<p><a href={resolve('/')}>Back to home</a></p>
