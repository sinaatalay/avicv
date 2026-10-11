import { error } from '@sveltejs/kit';
import { getRequestEvent } from '$app/server';
import { API_URL } from '$app/env/private';

/**
 * Sends a request to the FastAPI backend from inside a remote function (or
 * load function, action or hook). If the backend can't be reached or fails,
 * this throws an error that renders the nearest error boundary.
 */
export async function api(path: string, init?: RequestInit) {
	const { fetch } = getRequestEvent();
	let response: Response;

	try {
		response = await fetch(new URL(path, API_URL), init);
	} catch (cause) {
		console.error(`Could not reach the API at ${API_URL}`, cause);
		error(503, 'Could not reach the API');
	}

	if (response.status >= 500) error(502, 'The API returned an error');
	return response;
}

// The types below mirror the Pydantic models in backend/src/backend/resumes/router.py

export interface Resume {
	id: string;
	status: 'parsing' | 'matching' | 'done' | 'failed';
	error: string | null;
	filename: string | null;
	created_at: string;
	/** What parsing has found so far, while Gemini is still writing the CV */
	preview: { name: string | null; sections: string[] } | null;
	cv: Cv | null;
	/** The parsed CV as a RenderCV YAML file */
	rendercv_yaml: string | null;
	progress: { done: number; total: number };
	cost_usd: number;
}

export interface Cv {
	name: string | null;
	headline: string | null;
	location: string | null;
	sections: { title: string; entry_type: string; entries: (Record<string, unknown> | string)[] }[];
}

export interface Match {
	job_id: number;
	title: string;
	company: string | null;
	locations: string[];
	workplace_type: string | null;
	url: string | null;
	salary_min: number | null;
	salary_max: number | null;
	salary_currency: string | null;
	salary_interval: string | null;
	posted_at: string | null;
	/** Composite of the answers below, 0 to 1 */
	score: number;
	/** How well the candidate meets the core requirements, 0 to 4 */
	fit: number;
	/** Probability a recruiter would invite the candidate to interview */
	interview: number;
	/** Probability the candidate meets every explicit requirement */
	must_haves: number;
	/** Probability the candidate can take the job without relocating */
	location: number;
	seniority: 'below' | 'matches' | 'above';
}
