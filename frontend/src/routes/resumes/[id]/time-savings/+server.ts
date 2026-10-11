import { error } from '@sveltejs/kit';
import * as v from 'valibot';
import { api } from '#lib/server/api.ts';
import { timeSavingsSchema } from '#lib/time-savings.ts';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params }) => {
	if (!/^[a-f0-9]{32}$/.test(params.id)) error(404, 'CV not found');
	const headers = { 'Cache-Control': 'private, no-store' };
	const response = await api(`resumes/${params.id}/time-savings`);
	if (!response.ok) return Response.json({ message: 'Estimate unavailable.' }, { status: response.status, headers });
	const parsed = v.safeParse(timeSavingsSchema, await response.json());
	if (!parsed.success) error(502, 'Estimate unavailable.');
	return Response.json(parsed.output, { headers });
};
