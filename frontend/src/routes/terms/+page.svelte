<script lang="ts">
	import { resolve } from '$app/paths';
</script>

<svelte:head>
	<title>Terms · AviCV</title>
	<meta name="description" content="A few things to know before using AviCV." />
</svelte:head>

<article>
	<a class="back" href={resolve('/')}>← Back to AviCV</a>
	<h1>Terms of use</h1>
	<p class="lead">AviCV at avicv.com is an experimental hackathon project, provided as is. Use it at your own risk.</p>

	<h2>Check before you apply</h2>
	<p>AI can make mistakes. Check chat answers, review every tailored CV for accuracy, and check each job listing before applying. Matches do not guarantee an interview or a job. Time saved is an estimate.</p>

	<h2>Your responsibility</h2>
	<p>Only upload CVs you have permission to use. Do not misuse the service. Applications open on third-party sites, which have their own terms and privacy policies.</p>

	<h2>An early experiment</h2>
	<p>The service may change, stop working, or lose data. Keep your own copies. By uploading a CV, you agree to these terms and the processing described in our <a href={resolve('/privacy')}>privacy notice</a>.</p>
</article>

<style>
	article { max-width: 40rem; }
	.back { display: inline-block; margin-bottom: 1.6rem; font-size: 0.85rem; }
	h1 { margin: 0 0 0.8rem; font-size: clamp(1.8rem, 5vw, 2.4rem); line-height: 1.2; letter-spacing: -0.04em; }
	.lead { color: var(--color-muted); }
	h2 { margin: 1.7rem 0 0.4rem; font-size: 1rem; font-weight: 600; }
	p { margin: 0; font-size: 0.95rem; }
</style>
