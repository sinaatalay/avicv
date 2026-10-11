import { error } from '@sveltejs/kit';
import { api } from '#lib/server/api.ts';
import { privateJson, requireSameOrigin } from '#lib/server/private-json.ts';
import type { RequestHandler } from './$types';

export const DELETE: RequestHandler = async (event) => {
	requireSameOrigin(event);
	if (!/^[a-f0-9]{32}$/.test(event.params.id)) error(404, 'Connection not found');
	return privateJson(await api(`auth/tokens/${event.params.id}`, { method: 'DELETE' }));
};
