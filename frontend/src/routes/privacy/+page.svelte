<script lang="ts">
	import { resolve } from '$app/paths';
</script>

<svelte:head>
	<title>Privacy · AviCV</title>
	<meta name="description" content="How AviCV uses your CV and saves your job matches." />
</svelte:head>

<article>
	<a class="back" href={resolve('/')}>← Back to AviCV</a>
	<h1>Privacy</h1>
	<p class="lead">AviCV at avicv.com is an experimental hackathon project. Upload and use it at your own risk.</p>

	<h2>Your CV and results</h2>
	<p>We store your original PDF, extracted CV content, job matches, and generated CVs on our server to provide the service. Your browser also uses localStorage to remember recent CVs, results, preferences, and download progress.</p>

	<h2>Your browser and connected agents</h2>
	<p>A private cookie keeps this browser connected to your CVs. Clearing cookies can lose access to them. If you connect an agent, it can read your CVs and results and request job searches or tailored CVs. You can revoke its connection from the connection page. The agent you choose processes information under its own provider’s policies.</p>

	<h2>AI services</h2>
	<p>CV content and images are sent through <a href="https://openrouter.ai/privacy">OpenRouter</a> to Google Gemini to read your CV, reproduce its design, and tailor it to jobs. Professional details from your CV and job descriptions are sent to <a href="https://typesafe.ai/legal/privacy-policy">TypeSafe’s Jev</a> to find matches. These providers process that data under their own policies.</p>
	<p>Optional “Ask AviCV” chat uses CopilotKit. Your messages, tool results and relevant CV details are sent to the configured AI provider, such as OpenRouter or Anthropic. If you ask to discover external agents, the search can be sent to Darwin.</p>

	<h2>Before you upload</h2>
	<p>Only share information you’re comfortable sending to these services. “Remove” and “Clear list” on the homepage hide CVs in this browser; they do not delete server data or remove agent access. Keep connection tokens private and keep a copy of your original CV.</p>
</article>

<style>
	article { max-width: 40rem; }
	.back { display: inline-block; margin-bottom: 1.6rem; font-size: 0.85rem; }
	h1 { margin: 0 0 0.8rem; font-size: clamp(1.8rem, 5vw, 2.4rem); line-height: 1.2; letter-spacing: -0.04em; }
	.lead { color: var(--color-muted); }
	h2 { margin: 1.7rem 0 0.4rem; font-size: 1rem; font-weight: 600; }
	p { margin: 0; font-size: 0.95rem; }
</style>
