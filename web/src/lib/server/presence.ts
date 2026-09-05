import { createHash, timingSafeEqual } from 'node:crypto';

export type PresenceReport = { state: 'home' | 'away'; observedAt: string };

export function authorizedPresenceReport(header: string | null, token: string): boolean {
	if (!token || !header) return false;
	const digest = (value: string) => createHash('sha256').update(value).digest();
	return timingSafeEqual(digest(header), digest(`Bearer ${token}`));
}

export function parsePresenceReport(body: string, now: number): PresenceReport {
	if (Buffer.byteLength(body) > 512) throw new Error('Report too large');
	const fields = new URLSearchParams(body);
	if (
		[...fields.keys()].length !== 2 ||
		fields.getAll('state').length !== 1 ||
		fields.getAll('observedAt').length !== 1
	) throw new Error('Supply only state and observedAt');
	const state = fields.get('state');
	const observedAt = fields.get('observedAt');
	if (state !== 'home' && state !== 'away') throw new Error('State must be home or away');
	if (!observedAt || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(observedAt)) {
		throw new Error('observedAt must include a timezone');
	}
	const timestamp = Date.parse(observedAt);
	if (!Number.isFinite(timestamp) || timestamp > now) throw new Error('Invalid observation time');
	return { state, observedAt: new Date(timestamp).toISOString() };
}

export function presenceReportKey(report: PresenceReport): string {
	return createHash('sha256').update(`${report.state}\n${report.observedAt}`).digest('hex');
}
