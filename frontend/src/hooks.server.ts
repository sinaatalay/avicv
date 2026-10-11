import type { Handle } from '@sveltejs/kit/hooks';
import { API_URL } from '$app/env/private';
import { proxyIntegration } from '#lib/server/agent-proxy.ts';

const cookieName = 'avicv_session';

export const handle: Handle = async ({ event, resolve }) => {
	if (event.url.pathname === '/healthz') return resolve(event);
	// Agents authenticate explicitly; never create an anonymous owner for an MCP call.
	const publicProxy = await proxyIntegration(event, 'public');
	if (publicProxy) return publicProxy;
	const existing = event.cookies.get(cookieName);
	try {
		const response = await fetch(new URL('auth/session', API_URL.endsWith('/') ? API_URL : `${API_URL}/`), {
			method: 'POST', headers: existing ? { cookie: `${cookieName}=${existing}` } : {},
			signal: AbortSignal.timeout(5000), redirect: 'error'
		});
		if (response.ok) {
			const session = await response.json();
			const setCookie = response.headers.get('set-cookie');
			const value = setCookie?.match(/(?:^|,\s*)avicv_session=([^;]+)/)?.[1] ?? existing;
			if (typeof session.workspace_id === 'string' && /^[a-f0-9]{32}$/.test(session.workspace_id) && value) {
				event.locals.workspaceId = session.workspace_id;
				event.locals.sessionCookie = value;
				if (value !== existing) event.cookies.set(cookieName, value, {
					path: '/', httpOnly: true, sameSite: 'lax',
					secure: event.url.protocol === 'https:' || /(?:^|;)\s*secure(?:;|$)/i.test(setCookie ?? ''),
					maxAge: 365 * 24 * 60 * 60
				});
			}
		}
	} catch {
		// Public pages still work during an API outage. Protected calls report a clear error.
	}
	const session = event.locals.sessionCookie ?? existing;
	if (session) {
		// A one-way, non-authorizing partition keeps local previews available during API outages.
		const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(session));
		event.locals.cacheId = [...new Uint8Array(digest)].slice(0, 16).map((byte) => byte.toString(16).padStart(2, '0')).join('');
	}
	const authenticatedProxy = await proxyIntegration(event, 'authenticated');
	if (authenticatedProxy) return authenticatedProxy;
	return resolve(event);
};
