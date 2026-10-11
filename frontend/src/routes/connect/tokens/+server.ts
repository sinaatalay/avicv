import { error } from '@sveltejs/kit';
import { api } from '#lib/server/api.ts';
import { privateJson, requireSameOrigin } from '#lib/server/private-json.ts';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async () => privateJson(await api('auth/tokens'));
export const POST: RequestHandler = async (event) => {
	requireSameOrigin(event);
	const body = await event.request.json().catch(() => null);
	if (typeof body?.name !== 'string' || !body.name.trim() || body.name.length > 80) error(400, 'Choose a connection name');
	return privateJson(await api('auth/tokens', {
		method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: body.name.trim() })
	}));
};
