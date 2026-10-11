import { error } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import * as v from 'valibot';
import { api } from '#lib/server/api.ts';
import { tailoredStatusResponse } from '#lib/server/tailored-response.ts';
import type { RequestHandler } from './$types';

const inputSchema = v.object({ job_key: v.pipe(v.string(), v.minLength(1), v.maxLength(256)) });
export const POST: RequestHandler = async ({ params, request, url }) => {
	if (!/^[a-f0-9]{32}$/.test(params.id)) error(404, 'CV not found');
	if (request.headers.get('origin') && request.headers.get('origin') !== url.origin) error(403, 'Invalid request');
	let input: unknown;
	try { input = await request.json(); } catch { error(400, 'Invalid request'); }
	const parsed = v.safeParse(inputSchema, input);
	if (!parsed.success) error(400, 'Invalid job');
	const response = await api(`resumes/${params.id}/tailor`, {
		method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(parsed.output)
	});
	return tailoredStatusResponse(response, resolve('/resumes/[id]', { id: params.id }));
};
