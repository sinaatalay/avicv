import { error } from '@sveltejs/kit';
import * as v from 'valibot';
import { api } from '#lib/server/api.ts';
import { goodMatchLinksSchema } from '#lib/good-match-links.ts';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params }) => {
	if (!/^[a-f0-9]{32}$/.test(params.id)) error(404, 'CV not found');
	const headers = { 'Cache-Control': 'private, no-store' };
	const response = await api(`resumes/${params.id}/good-match-links`);
	if (!response.ok) return Response.json({ message: 'Could not load your links.' }, { status: response.status, headers });
	const parsed = v.safeParse(goodMatchLinksSchema, await response.json());
	if (!parsed.success) error(502, 'Could not load your links.');
	return Response.json(parsed.output, { headers });
};
