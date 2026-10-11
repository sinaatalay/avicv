import { error } from '@sveltejs/kit';
import { api } from '#lib/server/api.ts';
import { tailoredPdfResponse } from '#lib/server/tailored-response.ts';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params }) => {
	if (![params.id, params.artifactId].every((id) => /^[a-f0-9]{32}$/.test(id))) error(404, 'CV not found');
	return tailoredPdfResponse(await api(`resumes/${params.id}/tailored/${params.artifactId}/cv.pdf`));
};
