import { defineEnvVars } from '@sveltejs/kit/env';

export const variables = defineEnvVars({
	API_URL: {
		description: 'Base URL of the FastAPI backend, as reached from the SvelteKit server',
		schema: (value) => value || 'http://127.0.0.1:8000'
	}
});
