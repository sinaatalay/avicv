/** Shared browser/server contract for the résumé API. */
export type ResumeStatus = 'queued' | 'parsing' | 'matching' | 'waiting_for_jobs' | 'done' | 'partial' | 'failed';
export interface Progress {
	done: number;
	total: number;
	attempted: number;
	succeeded: number;
	failed: number;
	pending: number;
}
export interface SearchSummary {
	catalog_total: number;
	eligible_total: number;
	matching_postings: number;
	distinct_roles: number;
	missing_descriptions: number;
	shortlisted: number;
	retrieval_limit: number;
	candidate_location_known: boolean;
}
export interface Resume {
	id: string;
	results_are_previous: boolean;
	effective_location: string | null;
	location_source: 'resume' | 'default' | 'unknown';
	status: ResumeStatus;
	error: string | null;
	filename: string | null;
	created_at: string;
	preview: { name: string | null; sections: string[] } | null;
	cv: Cv | null;
	rendercv_yaml: string | null;
	progress: Progress;
	search: SearchSummary | null;
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
	job_key: string;
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
	score: number;
	fit: number;
	/** Model assessment, not a calibrated probability of an actual interview. */
	interview: number;
	must_haves: number;
	location: number | null;
	seniority: 'below' | 'matches' | 'above';
	catalog_status: 'open' | 'closed' | 'changed' | 'missing' | 'unverified';
	provider: string | null;
	board: string | null;
	source_id: string | null;
	/** Optional so browser previews written before source-health metadata remain readable. */
	source_status?: string;
	source_verified_at?: string | null;
	source_stale?: boolean;
}
export interface MatchResults {
	results_are_previous: boolean;
	effective_location: string | null;
	location_source: 'resume' | 'default' | 'unknown';
	items: Match[];
	total: number;
	unfiltered_total: number;
	closed_total: number;
	nearby_total: number | null;
	offset: number;
	limit: number;
	has_more: boolean;
	candidate_location_known: boolean;
}
export interface ResumeResults {
	resume: Resume;
	matches: MatchResults;
}
