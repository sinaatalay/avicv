import * as v from 'valibot';

// Shared by the `addItem` remote form (server-side validation) and its
// preflight (client-side validation). Matches `ItemCreate` in the backend.
export const newItem = v.object({
	name: v.pipe(
		v.string(),
		v.trim(),
		v.nonEmpty('Enter a name'),
		v.maxLength(100, 'Use 100 characters or fewer')
	)
});
