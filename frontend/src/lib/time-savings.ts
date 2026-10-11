import * as v from 'valibot';

const count = v.pipe(v.number(), v.integer(), v.minValue(0));
export const timeSavingsSchema = v.pipe(v.object({
	jobs_reviewed: count,
	cvs_tailored: count,
	job_review_seconds: count,
	cv_tailoring_seconds: count,
	estimated_seconds: count
}), v.check((value) => value.estimated_seconds === value.jobs_reviewed * value.job_review_seconds
	+ value.cvs_tailored * value.cv_tailoring_seconds, 'Inconsistent time estimate'));
export type TimeSavings = v.InferOutput<typeof timeSavingsSchema>;

/** Round down to whole minutes; never turn a partial minute into extra credit. */
export function formatSavedTime(seconds: number): string {
	const minutes = Math.floor(Math.max(0, seconds) / 60);
	if (minutes < 1) return '<1m';
	const hours = Math.floor(minutes / 60);
	const remainder = minutes % 60;
	return hours ? `${hours}h${remainder ? ` ${remainder}m` : ''}` : `${minutes}m`;
}

export function formatEstimateRate(seconds: number): string {
	return seconds < 60 ? `${seconds} sec` : `${seconds / 60} min`;
}
