import type { Match, ResumeResults } from '../src/lib/contracts.ts';
export const resumeId = 'a'.repeat(32);
export const match = (job_id = 1): Match => ({
	job_id, job_key: String(job_id), title: `Engineer ${job_id}`, company: 'Example', locations: ['Toronto, Canada'], workplace_type: 'remote',
	url: 'https://example.com/jobs/1', salary_min: null, salary_max: null, salary_currency: null,
	salary_interval: null, posted_at: null, score: 0.8, fit: 3, interview: 0.6, must_haves: 0.8,
	location: null, seniority: 'matches', catalog_status: 'open', provider: 'ashby', board: 'example', source_id: String(job_id)
});
export const result = (): ResumeResults => ({
	resume: { id: resumeId, results_are_previous: false, effective_location: null, location_source: 'unknown', status: 'done', error: null, filename: 'private-name.pdf', created_at: '2026-01-01',
		preview: { name: 'Private Name', sections: [] }, cv: { name: 'Private Name', headline: 'Engineer', location: null, sections: [] },
		rendercv_yaml: 'email: private@example.com', progress: { done: 1, total: 1, attempted: 1, succeeded: 1, failed: 0, pending: 0 },
		search: null, cost_usd: 0.01 },
	matches: { results_are_previous: false, effective_location: null, location_source: 'unknown', items: [match()], total: 1, unfiltered_total: 1, closed_total: 0, nearby_total: null, offset: 0, limit: 100,
		has_more: false, candidate_location_known: false }
});
