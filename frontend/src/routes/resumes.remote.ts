import { error, invalid, redirect } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import { form, query } from '$app/server';
import * as v from 'valibot';
import { api, type Match, type Resume } from '#lib/server/api.ts';
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

/**
 * Streams a résumé's progress: parsing, then matching, then the final ranking. Each value
 * carries the best matches so far. The stream ends once the résumé is done or has failed.
 */
export const watchResume = query.live(v.string(), async function* (id) {
	let previous = '';

	while (true) {
		const response = await api(`resumes/${encodeURIComponent(id)}`);
		if (response.status === 404) error(404, 'Résumé not found');
		const resume: Resume = await response.json();

		let matches: Match[] = [];
		if (resume.status === 'matching' || resume.status === 'done') {
			matches = await (await api(`resumes/${encodeURIComponent(id)}/matches?limit=100`)).json();
		}

		const value = { resume, matches };
		const serialized = JSON.stringify(value);
		if (serialized !== previous) {
			previous = serialized;
			yield value;
		}

		if (resume.status === 'done' || resume.status === 'failed') return;
		await new Promise((resolve) => setTimeout(resolve, 1000));
	}
});
