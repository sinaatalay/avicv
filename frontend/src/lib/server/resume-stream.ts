import type { MatchCategory, MatchResults, Resume, ResumeResults } from '../contracts.ts';

export interface ResultRequest { id: string; nearby: boolean; limit: number; category?: MatchCategory }
type Request = (path: string, init: RequestInit) => Promise<Response>;

/** The timeout and its listener are both released when a client disconnects. */
export function waitForPoll(signal: AbortSignal, milliseconds = 1000): Promise<void> {
	return new Promise((resolve) => {
		if (signal.aborted) return resolve();
		const finish = () => {
			clearTimeout(timer);
			signal.removeEventListener('abort', finish);
			resolve();
		};
		const timer = setTimeout(finish, milliseconds);
		signal.addEventListener('abort', finish, { once: true });
	});
}

/** Refetch the requested ranking window on each update; reconcile stable keys if a live rank changes between pages. */
export async function* streamResume(
	input: ResultRequest, signal: AbortSignal, request: Request,
	pause: typeof waitForPoll = waitForPoll
): AsyncGenerator<ResumeResults> {
	let previous = '';
	try {
		while (!signal.aborted) {
			const response = await request(`resumes/${encodeURIComponent(input.id)}`, { signal });
			if (!response.ok) throw new Error(response.status === 404 ? 'Résumé not found' : 'Could not load the résumé');
			const resume: Resume = await response.json();
			let matches: MatchResults = { items: [], total: 0, unfiltered_total: 0, good_total: 0, other_total: 0, closed_total: 0, nearby_total: null,
				offset: 0, limit: input.limit, has_more: false, candidate_location_known: false, results_are_previous: resume.results_are_previous, effective_location: resume.effective_location, location_source: resume.location_source };
			if (resume.progress.total > 0 || ['done', 'partial', 'matching', 'failed'].includes(resume.status)) {
				let offset = 0;
				do {
					const params = new URLSearchParams({ nearby: String(input.nearby), limit: String(Math.min(500, input.limit - offset)), offset: String(offset), category: input.category ?? 'all' });
					const pageResponse = await request(`resumes/${encodeURIComponent(input.id)}/match-results?${params}`, { signal });
					if (!pageResponse.ok) throw new Error('Could not load matches');
					const page: MatchResults = await pageResponse.json();
					const items = new Map([...matches.items, ...page.items].map((item) => [item.job_key, item]));
					matches = { ...page, items: [...items.values()], offset: 0, limit: input.limit };
					offset += page.items.length;
					if (!page.items.length || !page.has_more) break;
				} while (!signal.aborted && offset < input.limit);
			}
			if (signal.aborted) return;
			const value = { resume, matches };
			const serialized = JSON.stringify(value);
			if (serialized !== previous) { previous = serialized; yield value; }
			if (['done', 'partial', 'failed'].includes(resume.status)) return;
			await pause(signal);
		}
	} catch (cause) {
		if (!signal.aborted) throw cause;
	}
}
