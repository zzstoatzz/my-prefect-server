import { createHash, timingSafeEqual } from 'node:crypto';
import { z } from 'zod';

const presenceReport = z.object({
	state: z.enum(['home', 'away']),
	observedAt: z.string()
}).strict();

export type PresenceReport = z.infer<typeof presenceReport>;

export function authorizedPresenceReport(header: string | null, token: string): boolean {
	if (!token || !header) return false;
	const digest = (value: string) => createHash('sha256').update(value).digest();
	return timingSafeEqual(digest(header), digest(`Bearer ${token}`));
}

export function parsePresenceReport(body: string, now: number): PresenceReport {
	if (Buffer.byteLength(body) > 512) throw new Error('Report too large');
	const { state, observedAt } = presenceReport.parse(JSON.parse(body));
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
