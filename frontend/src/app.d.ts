// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	namespace App {
		// interface Error {}
		interface Locals {
			sessionCookie?: string;
			workspaceId?: string;
			cacheId?: string;
		}
		interface PageData { workspaceId: string | null; cacheId: string | null; }
		// interface PageState {}
		// interface Platform {}
	}
}

export {};
