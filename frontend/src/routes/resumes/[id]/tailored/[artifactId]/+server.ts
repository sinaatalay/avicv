import { error } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import { api } from '#lib/server/api.ts';
import { tailoredStatusResponse } from '#lib/server/tailored-response.ts';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params }) => {
	if (![params.id, params.artifactId].every((id) => /^[a-f0-9]{32}$/.test(id))) error(404, 'CV not found');
	const response = await api(`resumes/${params.id}/tailored/${params.artifactId}`);
	return tailoredStatusResponse(response, resolve('/resumes/[id]', { id: params.id }));
};
