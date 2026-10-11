<script lang="ts">
	import { addItem, deleteItem, getItems } from './items.remote.ts';
	import { newItem } from './schema.ts';

	let deleteFailed = $state(false);

	async function remove(id: number) {
		deleteFailed = false;
		try {
			await deleteItem(id);
		} catch {
			deleteFailed = true;
		}
	}
</script>

<svelte:head>
	<title>Items · Speedhack</title>
	<meta name="description" content="Items stored by the FastAPI backend." />
</svelte:head>

<h1>Items</h1>

<p>
	These items live in the FastAPI backend. The page talks to it through SvelteKit
	<a href="https://svelte.dev/docs/kit/remote-functions">remote functions</a>, which run on the
	SvelteKit server, so the browser never calls the API directly.
</p>

<form {...addItem.preflight(newItem)}>
	<label>
		Name
		<input {...addItem.fields.name.as('text')} autocomplete="off" />
	</label>
	<button disabled={!!addItem.pending}>Add item</button>
</form>

{#each addItem.fields.name.issues() as issue (issue.message)}
	<p class="error">{issue.message}</p>
{/each}

<svelte:boundary>
	{const items = $derived(await getItems())}

	{#if items.length > 0}
		<ul>
			{#each items as item (item.id)}
				<li>
					{item.name}
					<button type="button" aria-label="Delete {item.name}" onclick={() => remove(item.id)}>
						Delete
					</button>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="muted">No items yet. Add one above.</p>
	{/if}

	{#if deleteFailed}
		<p class="error">Couldn't delete the item. Try again.</p>
	{/if}

	<!-- SvelteKit passes rendering errors through handleError, so this is an App.Error -->
	{#snippet failed(error, reset)}
		<p class="error">{(error as App.Error).message}</p>
		<button
			onclick={() => {
				// refetch rather than re-render the failed result, then rebuild the boundary
				void getItems().refresh();
				reset();
			}}
		>
			Try again
		</button>
	{/snippet}
</svelte:boundary>

<style>
	form {
		display: flex;
		align-items: end;
		gap: 0.75rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.875rem;
		color: var(--color-muted);
	}

	input {
		width: 16rem;
		padding: 0.5rem 0.75rem;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		color: var(--color-text);
		background-color: var(--color-bg);
	}

	input[aria-invalid='true'] {
		border-color: var(--color-accent);
	}

	button {
		padding: 0.5rem 1rem;
		border: 1px solid var(--color-border);
		border-radius: 0.5rem;
		color: inherit;
		background-color: var(--color-surface);
		cursor: pointer;
	}

	button:hover {
		color: var(--color-accent);
	}

	ul {
		padding: 0;
		list-style: none;
	}

	li {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding-block: 0.5rem;
		border-bottom: 1px solid var(--color-border);
	}

	li button {
		padding: 0.25rem 0.75rem;
		font-size: 0.875rem;
	}

	.error {
		color: var(--color-accent);
	}

	.muted {
		color: var(--color-muted);
	}
</style>
