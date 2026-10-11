import { error } from '@sveltejs/kit';
import { api } from '#lib/server/api.ts';
import { pageQuery, privateJson } from '#lib/server/private-json.ts';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params, url }) => {
	if (!/^[a-f0-9]{32}$/.test(params.id)) error(404, 'CV not found');
	return privateJson(await api(`resumes/${params.id}/tailored?${pageQuery(url)}`));
};
