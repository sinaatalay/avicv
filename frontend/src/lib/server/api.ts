import { error } from '@sveltejs/kit';
import { getRequestEvent } from '$app/server';
import { API_URL } from '$app/env/private';

/** Mirrors the `Item` model in backend/src/backend/routers/items.py */
export interface Item {
	id: number;
	name: string;
}

/**
 * Sends a request to the FastAPI backend from inside a remote function (or
 * load function, action or hook). If the backend can't be reached or fails,
 * this throws an error that renders the nearest error boundary.
 */
export async function api(path: string, init?: RequestInit) {
	const { fetch } = getRequestEvent();
	let response: Response;

	try {
		response = await fetch(new URL(path, API_URL), init);
	} catch (cause) {
		console.error(`Could not reach the API at ${API_URL}`, cause);
		error(503, 'Could not reach the API');
	}

	if (response.status >= 500) error(502, 'The API returned an error');
	return response;
}
