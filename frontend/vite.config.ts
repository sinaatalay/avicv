import adapter from '@sveltejs/adapter-bun';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true,

				// `await` in components, required by remote functions
				experimental: { async: true }
			},

			// Remote functions (query, form, command) in `*.remote.ts` files
			experimental: { remoteFunctions: true },

			// Builds a standalone Bun server into `build/`. Run it with `bun ./build`.
			// See https://svelte.dev/docs/kit/adapter-bun for options.
			adapter: adapter()
		})
	]
});
