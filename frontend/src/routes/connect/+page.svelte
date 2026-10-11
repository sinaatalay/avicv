<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { Check, Copy, KeyRound, Plug, X } from '@lucide/svelte';
	import * as v from 'valibot';
	import { agentSetup, clients, connectionListSchema, issuedConnectionSchema, type AgentClient, type Connection } from '#lib/agent-connections.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	let client = $state<AgentClient>('codex');
	let origin = $state('https://avicv.com');
	let connections = $state<Connection[]>([]);
	let issued = $state<{ id: string; token: string } | null>(null);
	let creating = $state(false);
	let revoking = $state<string | null>(null);
	let copied = $state('');
	let problem = $state('');
	let loaded = $state(false);
	const setup = $derived(agentSetup(client, origin));
	const selected = $derived(clients.find((item) => item.id === client)!);
	const active = $derived(connections.filter((connection) => !connection.revoked_at));
	const exportCommand = 'read -rs AVICV_TOKEN; export AVICV_TOKEN';
	$effect(() => { void data.workspaceId; issued = null; connections = []; loaded = false; problem = ''; });

	onMount(() => {
		origin = window.location.origin;
		const controller = new AbortController();
		let refreshing = false;
		async function refresh() {
			if (document.hidden || refreshing) return;
			const workspaceId = data.workspaceId;
			refreshing = true;
			try {
				const response = await fetch(resolve('/connect/tokens'), { cache: 'no-store', signal: controller.signal });
				if (!response.ok) throw new Error('Connections unavailable');
				const list = v.parse(connectionListSchema, await response.json());
				if (workspaceId !== data.workspaceId) return;
				connections = list.items; loaded = true;
			} catch { if (!controller.signal.aborted && !loaded) problem = 'Your connections couldn’t be loaded. Please try again shortly.'; }
			finally { refreshing = false; }
		}
		void refresh();
		window.addEventListener('focus', refresh);
		document.addEventListener('visibilitychange', refresh);
		const timer = setInterval(refresh, 15000);
		return () => { controller.abort(); clearInterval(timer); window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', refresh); };
	});
	async function createConnection() {
		if (creating) return;
		creating = true; problem = ''; copied = '';
		try {
			const response = await fetch(resolve('/connect/tokens'), {
				method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: selected.name })
			});
			if (response.status === 429) { problem = 'Revoke an old connection before creating another.'; return; }
			if (!response.ok) throw new Error('Connection unavailable');
			const connection = v.parse(issuedConnectionSchema, await response.json());
			issued = { id: connection.id, token: connection.token };
			const { token: _token, ...metadata } = connection;
			connections = [metadata, ...connections]; loaded = true;
		} catch { problem = 'We couldn’t create your connection. Please try again.'; }
		finally { creating = false; }
	}
	async function revoke(id: string) {
		revoking = id; problem = '';
		try {
			const response = await fetch(resolve('/connect/tokens/[id]', { id }), { method: 'DELETE' });
			if (!response.ok) throw new Error('Revoke failed');
			connections = connections.filter((connection) => connection.id !== id);
			if (issued?.id === id) issued = null;
		} catch { problem = 'We couldn’t revoke this connection. Please try again.'; }
		finally { revoking = null; }
	}
	async function copy(text: string, kind: string) {
		try { await navigator.clipboard.writeText(text); copied = kind; }
		catch { problem = 'Clipboard unavailable. Select the text and copy it manually.'; }
	}
</script>

<svelte:head><title>Use AviCV in your agent · AviCV</title><meta name="description" content="Connect your agent to your AviCV jobs and tailored CVs." /></svelte:head>

<h1>Take your CVs into your agent</h1>
<p class="lead">Use your CVs, job matches and tailored PDFs here or in your agent. Your chats stay separate.</p>

<div class="client-picker" role="group" aria-label="Choose your agent">
	{#each clients as item}<button class:chosen={client === item.id} aria-pressed={client === item.id} onclick={() => { client = item.id; copied = ''; }}>{item.name}</button>{/each}
</div>

<section class="setup" aria-label="Connect your agent">
	<h2><Plug size={19} aria-hidden="true" /> Connect {selected.name}</h2>
	<p>A connection gives this agent access to this workspace’s CVs and jobs. Revoke it below whenever you like.</p>
	{#if !issued}
		<button class="primary" disabled={creating || !loaded} onclick={() => void createConnection()}><KeyRound size={16} aria-hidden="true" />{!loaded ? 'Loading connections…' : creating ? 'Creating…' : 'Create connection token'}</button>
	{:else}
		<label class="token-label" for="connection-token">Your token · shown only on this page</label>
		<div class="token-row"><input id="connection-token" type="password" readonly value={issued.token} autocomplete="off" spellcheck="false" onclick={(event) => event.currentTarget.select()} /><button class="secondary" onclick={() => void copy(issued!.token, 'token')}>{#if copied === 'token'}<Check size={16} />Copied{:else}<Copy size={16} />Copy token{/if}</button></div>
		<p class="small">Keep it private. It won’t be shown again after you leave or refresh.</p>
		{#if client !== 'vscode'}
			<p>First, run this command, paste your token, then press Enter.</p>
			<div class="code-block"><pre>{exportCommand}</pre><button class="copy" aria-label="Copy token setup command" onclick={() => void copy(exportCommand, 'environment')}>{#if copied === 'environment'}<Check size={16} />{:else}<Copy size={16} />{/if}</button></div>
		{/if}
		<p class="code-label">{setup.label}</p>
		<div class="code-block"><pre>{setup.text}</pre><button class="copy" aria-label="Copy agent configuration" onclick={() => void copy(setup.text, 'setup')}>{#if copied === 'setup'}<Check size={16} />{:else}<Copy size={16} />{/if}</button></div>
		<p class="small">{setup.note}</p>
		<p>Then ask your agent: <strong>“Show my AviCV CVs and good job matches.”</strong></p>
		<button class="text-button" onclick={() => { issued = null; copied = ''; }}>Set up another agent</button>
	{/if}
</section>

{#if problem}<p class="problem" role="alert">{problem}</p>{/if}
{#if loaded && active.length}
	<section class="connections" aria-label="Your connections">
		<h2>Your connections</h2>
		<ul>{#each active as connection (connection.id)}<li>
			<div><strong>{connection.name}</strong><span>{connection.last_used_at ? `Last used ${new Date(connection.last_used_at).toLocaleString()}` : 'Not used yet'}</span></div>
			<button class="text-button revoke" disabled={revoking !== null} onclick={() => void revoke(connection.id)} aria-label={`Revoke ${connection.name} connection`}><X size={15} aria-hidden="true" />{revoking === connection.id ? 'Revoking…' : 'Revoke'}</button>
		</li>{/each}</ul>
		<p class="small">“Last used” means your agent made an authenticated request. It doesn’t mean it is online now.</p>
	</section>
{/if}
<p class="small persistence">No account needed. Keep this browser’s cookies to return to these CVs. Removing a CV from your home page only hides it in this browser.</p>

<style>
	h1 { font-size: clamp(1.8rem, 5vw, 2.3rem); line-height: 1.2; letter-spacing: -0.035em; margin: 0 0 0.8rem; }
	.lead { color: var(--color-muted); max-width: 38rem; }
	h2 { display: flex; align-items: center; gap: 0.55rem; font-size: 1.05rem; margin: 0 0 0.8rem; }
	p { font-size: 0.9rem; }
	.client-picker { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 1.75rem 0 1rem; }
	button { cursor: pointer; }
	button:disabled { opacity: 0.55; cursor: default; }
	.client-picker button { background: transparent; color: var(--color-muted); border: 1px solid var(--color-border); padding: 0.55rem 0.9rem; border-radius: 0.5rem; font-size: 0.85rem; }
	.client-picker .chosen { border-color: var(--color-accent); color: var(--color-accent); background: var(--color-surface); }
	.setup { padding: 1.5rem; border: 1px solid var(--color-border); border-radius: 0.75rem; }
	.setup > p:first-of-type { color: var(--color-muted); }
	.primary, .secondary { display: inline-flex; justify-content: center; align-items: center; gap: 0.5rem; padding: 0.6rem 0.9rem; border-radius: 0.45rem; font-size: 0.85rem; font-weight: 600; }
	.primary { background: var(--color-accent); color: var(--color-bg); border: 1px solid var(--color-accent); }
	.secondary { background: var(--color-bg); color: var(--color-text); border: 1px solid var(--color-border); flex: none; }
	.token-label { display: block; font-size: 0.85rem; font-weight: 600; margin-top: 1.5rem; }
	.token-row { display: flex; gap: 0.5rem; margin-top: 0.4rem; }
	.token-row input { min-width: 0; width: 100%; border: 1px solid var(--color-border); border-radius: 0.45rem; padding: 0.6rem; background: var(--color-bg); color: var(--color-text); }
	.small { font-size: 0.8rem; color: var(--color-muted); }
	.code-label { margin-bottom: 0.4rem; }
	.code-block { position: relative; background: var(--color-surface); border-radius: 0.5rem; }
	pre { white-space: pre-wrap; overflow-wrap: anywhere; padding: 0.85rem 2.75rem 0.85rem 0.85rem; font-size: 0.75rem; line-height: 1.6; }
	.copy { position: absolute; top: 0.55rem; right: 0.5rem; display: flex; padding: 0.3rem; border: 0; background: none; color: var(--color-muted); }
	.text-button { border: 0; background: none; padding: 0; color: var(--color-accent); font-size: 0.8rem; text-decoration: underline; }
	.connections { margin-top: 2rem; }
	.connections ul { list-style: none; padding: 0; }
	.connections li { display: flex; justify-content: space-between; gap: 1rem; align-items: center; padding: 0.8rem 0; border-bottom: 1px solid var(--color-border); font-size: 0.9rem; }
	.connections span { display: block; font-size: 0.75rem; color: var(--color-muted); }
	.revoke { display: inline-flex; align-items: center; gap: 0.2rem; }
	.problem { color: var(--color-accent); }
	.persistence { margin-top: 2rem; }
</style>
