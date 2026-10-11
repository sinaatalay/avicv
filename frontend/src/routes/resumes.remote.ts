import { error, invalid, redirect } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import { command, form, getRequestEvent, query } from '$app/server';
import * as v from 'valibot';
import { api } from '#lib/server/api.ts';
import { streamResume } from '#lib/server/resume-stream.ts';
import { resumeUpload } from './schema.ts';

/** Hands the PDF to the backend, which parses and matches it in the background */
export const uploadResume = form(resumeUpload, async ({ resume }, issue) => {
	// Copy the upload into a real File: SvelteKit's server-side stand-in isn't a Blob that FormData takes
	const file = new File([await resume.arrayBuffer()], resume.name, { type: resume.type });
	const body = new FormData();
	body.append('file', file);
	const response = await api('resumes', { method: 'POST', body });

	if (!response.ok) {
		const { detail } = await response.json();
		invalid(issue.resume(typeof detail === 'string' ? detail : 'Upload your résumé as a PDF'));
	}

	const { id }: { id: string } = await response.json();
	redirect(303, resolve('/resumes/[id]', { id }));
});

const resumeId = v.pipe(v.string(), v.regex(/^[a-f0-9]{32}$/));

export const watchResume = query.live(v.object({
	id: resumeId,
	nearby: v.boolean(),
	category: v.picklist(['all', 'good', 'other']),
	limit: v.pipe(v.number(), v.integer(), v.minValue(1), v.maxValue(10000))
}), async function* (input) {
	const { request } = getRequestEvent();
	yield* streamResume(input, request.signal, api);
});

export const retryResume = command(resumeId, async (id) => {
	const response = await api(`resumes/${id}/retry`, { method: 'POST' });
	if (!response.ok) error(response.status, 'Could not retry this résumé');
});

export const rematchResume = command(v.object({
	id: resumeId,
	location: v.optional(v.pipe(v.string(), v.trim(), v.minLength(2), v.maxLength(200)))
}), async ({ id, location }) => {
	const response = await api(`resumes/${id}/rematch`, {
		method: 'POST', headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(location ? { location } : {})
	});
	if (!response.ok) error(response.status, 'Could not refresh matches');
});

export const deleteResume = command(resumeId, async (id) => {
	const response = await api(`resumes/${id}`, { method: 'DELETE' });
	if (!response.ok && response.status !== 404) error(response.status, 'Could not delete this résumé');
});
