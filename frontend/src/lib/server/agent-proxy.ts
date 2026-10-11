import type { RequestEvent } from '@sveltejs/kit';
import { AGENT_URL, API_URL } from '$app/env/private';

const forwardedHeaders = [
	'accept', 'content-type', 'authorization', 'origin', 'mcp-protocol-version',
	'mcp-session-id', 'mcp-method', 'mcp-name', 'last-event-id', 'sec-fetch-site'
];

/** One public origin in development and production; internal services never need public ports. */
export async function proxyIntegration(event: RequestEvent, phase: 'public' | 'authenticated'): Promise<Response | null> {
	const { pathname, search } = event.url;
	const backend = pathname === '/mcp' || pathname === '/mcp/'
		|| pathname.startsWith('/uploads/') || pathname.startsWith('/downloads/');
	const agent = pathname === '/chat' || pathname.startsWith('/chat/')
		|| pathname === '/api/copilotkit' || pathname.startsWith('/api/copilotkit/');
	if (!(phase === 'public' ? backend : agent)) return null;
	// Bun can stream precompressed chat assets without decompressing them first.
	const compressed = agent && pathname.startsWith('/chat/') && 'Bun' in globalThis;
	const headers = new Headers();
	for (const name of forwardedHeaders) {
		const value = event.request.headers.get(name);
		if (value !== null) headers.set(name, value);
	}
	if (compressed) headers.set('accept-encoding', event.request.headers.get('accept-encoding') ?? 'identity');
	const cookie = event.cookies.get('avicv_session');
	if (cookie) headers.set('cookie', `avicv_session=${encodeURIComponent(cookie)}`);
	const route = pathname === '/mcp/' ? '/mcp' : pathname;
	const target = new URL(`${route}${search}`, backend ? API_URL : AGENT_URL);
	try {
		const options: RequestInit & { duplex: string; decompress?: boolean } = {
			method: event.request.method, headers, redirect: 'manual', signal: event.request.signal,
			body: ['GET', 'HEAD'].includes(event.request.method) ? undefined : event.request.body,
			// A streaming body keeps PDF uploads and MCP SSE out of application memory.
			duplex: 'half',
			...(compressed ? { decompress: false } : {})
		};
		const response = await fetch(target, options);
		const outgoing = new Headers(response.headers);
		for (const name of ['connection', 'transfer-encoding', 'content-length', 'set-cookie']) outgoing.delete(name);
		if (!compressed) outgoing.delete('content-encoding');
		outgoing.set('cache-control', 'private, no-store');
		return new Response(response.body, { status: response.status, headers: outgoing });
	} catch {
		return Response.json({ message: 'This service is temporarily unavailable. Try again shortly.' }, { status: 503 });
	}
}
