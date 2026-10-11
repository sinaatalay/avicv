import { api } from '#lib/server/api.ts';
import { pageQuery, privateJson } from '#lib/server/private-json.ts';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ url }) => privateJson(await api(`resumes?${pageQuery(url)}`));
