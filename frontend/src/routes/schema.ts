import * as v from 'valibot';

/**
 * `v.file()` checks `instanceof File`. On the server SvelteKit hands uploads over as proxies that
 * only claim `File.prototype` through a `getPrototypeOf` trap, which Bun's `instanceof` ignores,
 * so check the shape instead.
 */
const isFile = (value: unknown): value is File =>
	typeof value === 'object' &&
	value !== null &&
	typeof (value as File).name === 'string' &&
	typeof (value as File).arrayBuffer === 'function';

/** Shared by the `uploadResume` form and its preflight check in the browser */
export const resumeUpload = v.object({
	resume: v.pipe(
		v.custom<File>(isFile, 'Choose a PDF'),
		v.mimeType(['application/pdf'], 'Upload your résumé as a PDF'),
		v.maxSize(10 * 1024 * 1024, 'Upload a PDF smaller than 10 MB')
	)
});
