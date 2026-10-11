import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = ({ locals, setHeaders, url }) => {
	// Recheck session context when navigating between pages, including recovery after an outage.
	void url.pathname;
	setHeaders({ 'cache-control': 'private, no-store' });
	return { workspaceId: locals.workspaceId ?? null, cacheId: locals.cacheId ?? null };
};
