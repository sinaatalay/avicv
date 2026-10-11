import { API_URL, AGENT_URL } from '$app/env/private';

/** Readiness checks both private services without creating a user workspace. */
export async function GET() {
	try {
		const responses = await Promise.all([API_URL, AGENT_URL].map((base) => fetch(new URL('/health', base), { signal: AbortSignal.timeout(2500) })));
		const healthy = responses.every((response) => response.ok);
		return Response.json({ status: healthy ? 'ok' : 'unavailable' }, { status: healthy ? 200 : 503 });
	} catch { return Response.json({ status: 'unavailable' }, { status: 503 }); }
}
