import { describe, expect, test } from 'bun:test';
import * as v from 'valibot';
import { formatSavedTime, timeSavingsSchema } from '../src/lib/time-savings.ts';

describe('honest time estimates', () => {
	test('formats durations without rounding partial minutes into extra savings', () => {
		expect(formatSavedTime(30)).toBe('<1m');
		expect(formatSavedTime(119)).toBe('1m');
		expect(formatSavedTime(3599)).toBe('59m');
		expect(formatSavedTime(3600)).toBe('1h');
		expect(formatSavedTime(3900)).toBe('1h 5m');
		expect(formatSavedTime(4500)).toBe('1h 15m');
	});
	test('rejects exaggerated or invalid totals instead of displaying them', () => {
		const value = { jobs_reviewed: 130, cvs_tailored: 1, job_review_seconds: 30, cv_tailoring_seconds: 600, estimated_seconds: 4500 };
		expect(v.safeParse(timeSavingsSchema, value).success).toBe(true);
		expect(v.safeParse(timeSavingsSchema, { ...value, estimated_seconds: 100000 }).success).toBe(false);
		expect(v.safeParse(timeSavingsSchema, { ...value, cvs_tailored: -1 }).success).toBe(false);
		expect(v.safeParse(timeSavingsSchema, { ...value, jobs_reviewed: 130.5 }).success).toBe(false);
	});
});
