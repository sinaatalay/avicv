import { error } from '@sveltejs/kit';
import { getRequestEvent } from '$app/server';
import { API_URL } from '$app/env/private';

/**
 * Sends a request to the FastAPI backend from inside a remote function (or
 * load function, action or hook). If the backend can't be reached or fails,
 * this throws an error that renders the nearest error boundary.
 */
export async function api(path: string, init?: RequestInit) {
	const { fetch, request, locals } = getRequestEvent();
	if (!locals.sessionCookie || !locals.workspaceId) error(503, 'Could not connect to your CVs. Please try again.');
	const signal = init?.signal ?? request.signal;
	const headers = new Headers(init?.headers);
	// This app forwards exactly its own session, never other cookies or a caller's bearer token.
	headers.set('cookie', `avicv_session=${locals.sessionCookie}`);
	headers.delete('authorization');
	if (!['GET', 'HEAD'].includes((init?.method ?? 'GET').toUpperCase())) {
		for (const name of ['origin', 'sec-fetch-site']) {
			const value = request.headers.get(name);
			if (value) headers.set(name, value);
		}
	}
	let response: Response;

	try {
		response = await fetch(new URL(path, API_URL.endsWith('/') ? API_URL : `${API_URL}/`), { ...init, headers, signal });
	} catch (cause) {
		if (signal.aborted) throw cause;
		console.error(`Could not reach the API at ${API_URL}`, cause);
		error(503, 'Could not reach the API');
	}

	if (response.status >= 500) error(502, 'The API returned an error');
	return response;
}
