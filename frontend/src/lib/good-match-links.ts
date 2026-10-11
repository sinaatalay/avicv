import * as v from 'valibot';

function applicationLink(value: string): boolean {
	if (/[\s\u0000-\u001f\u007f]/.test(value)) return false;
	try {
		const url = new URL(value);
		return ['https:', 'http:'].includes(url.protocol) && Boolean(url.hostname);
	} catch { return false; }
}

export const goodMatchLinksSchema = v.object({
	links: v.pipe(v.array(v.pipe(v.string(), v.maxLength(8192), v.check(applicationLink))), v.maxLength(10000))
});

export class NoApplicationLinks extends Error {}

/** Fetch one complete ranked snapshot, independent of the cards loaded on screen. */
export async function loadGoodMatchLinks(path: string, signal: AbortSignal): Promise<string[]> {
	const response = await fetch(path, { signal, cache: 'no-store' });
	if (!response.ok) throw new Error('Could not load application links');
	const { links } = v.parse(goodMatchLinksSchema, await response.json());
	if (!links.length) throw new NoApplicationLinks();
	return [...new Set(links)];
}
