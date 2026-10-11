/** Forward a PDF stream without buffering it or exposing the backend address. */
export function tailoredPdfResponse(response: Response): Response {
	const headers = new Headers({ 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' });
	if (!response.ok) return Response.json({ message: 'Your CV is not ready to download.' }, { status: response.status, headers });
	if (!response.headers.get('Content-Type')?.toLowerCase().startsWith('application/pdf')) {
		return Response.json({ message: 'Your CV could not be downloaded.' }, { status: 502, headers });
	}
	const filename = response.headers.get('Content-Disposition')?.match(/filename="([a-zA-Z0-9 ._()-]+\.pdf)"/i)?.[1];
	headers.set('Content-Type', 'application/pdf');
	headers.set('Content-Disposition', `attachment; filename="${filename ? filename.slice(0, -4).slice(0, 190) + '.pdf' : 'Tailored-CV.pdf'}"`);
	return new Response(response.body, { status: 200, headers });
}
import * as v from 'valibot';
import { tailoredCvSchema } from '../tailored-cvs.ts';

export async function tailoredStatusResponse(response: Response, resumePath: string): Promise<Response> {
	const headers = { 'Cache-Control': 'private, no-store' };
	if (!response.ok) {
		const body = await response.json().catch(() => null);
		const detail = response.status < 500 && typeof body?.detail === 'string' && body.detail.length <= 300
			? body.detail : 'Your CV could not be prepared.';
		return Response.json({ message: detail }, { status: response.status, headers });
	}
	const parsed = v.safeParse(tailoredCvSchema, await response.json());
	if (!parsed.success) return Response.json({ message: 'Your CV could not be prepared.' }, { status: 502, headers });
	const artifact = parsed.output;
	return Response.json({ ...artifact, error: artifact.error ? 'Your CV could not be prepared.' : null,
		download_url: artifact.status === 'ready' ? `${resumePath}/tailored/${artifact.id}/cv.pdf` : null
	}, { status: response.status, headers });
}
