import { invalid } from '@sveltejs/kit';
import { command, form, query } from '$app/server';
import * as v from 'valibot';
import { api, type Item } from '#lib/server/api.ts';
import { newItem } from './schema.ts';

export const getItems = query(async () => {
	const response = await api('items/');
	const items: Item[] = await response.json();
	return items;
});

export const addItem = form(newItem, async ({ name }, issue) => {
	const response = await api('items/', {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ name })
	});

	// FastAPI validates too: map its 422 response back onto the field
	if (response.status === 422) {
		const { detail } = await response.json();
		invalid(issue.name(String(detail[0].msg)));
	}

	// Send the updated list back in the same response (single-flight mutation)
	void getItems().refresh();
});

export const deleteItem = command(v.number(), async (id) => {
	await api(`items/${id}`, { method: 'DELETE' });
	void getItems().refresh();
});
