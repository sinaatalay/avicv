import { error, json } from '@sveltejs/kit';
import type { RequestEvent } from '@sveltejs/kit';

export function requireSameOrigin({ request, url }: Pick<RequestEvent, 'request' | 'url'>) {
	if (request.headers.get('sec-fetch-site') === 'cross-site'
		|| (request.headers.get('origin') && request.headers.get('origin') !== url.origin)) error(403, 'Invalid request');
}

export async function privateJson(response: Response) {
	if (!response.ok) {
		const status = response.status >= 400 && response.status < 500 ? response.status : 502;
		return json({ message: status === 401 ? 'Reconnect to your CVs and try again.' : 'Could not complete this request. Please try again.' }, {
			status, headers: { 'cache-control': 'private, no-store' }
		});
	}
	if (response.status === 204) return new Response(null, { status: 204, headers: { 'cache-control': 'private, no-store' } });
	return json(await response.json(), { status: response.status, headers: { 'cache-control': 'private, no-store' } });
}

export function pageQuery(url: URL) {
	const offset = url.searchParams.get('offset') ?? '0';
	if (!/^\d{1,7}$/.test(offset)) error(400, 'Invalid page');
	return `offset=${offset}&limit=100`;
}
