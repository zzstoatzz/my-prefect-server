import { env } from '$env/dynamic/private';
import { error, json } from '@sveltejs/kit';
import { authorizedPresenceReport, parsePresenceReport, presenceReportKey } from '$lib/server/presence';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ request, fetch }) => {
	const token = env.PRESENCE_WEBHOOK_TOKEN;
	const api = env.PREFECT_API_URL;
	const auth = env.PREFECT_API_AUTH_STRING;
	const deployment = env.PRESENCE_DEPLOYMENT_ID;
	if (!token || !api || !auth || !deployment) error(503, 'Presence reporting is not configured');
	if (!authorizedPresenceReport(request.headers.get('authorization'), token)) error(401, 'Unauthorized');
	const contentType = request.headers.get('content-type')?.split(';')[0].trim();
	if (contentType !== 'application/json') error(415, 'Use JSON with state and observedAt');
	const report = await (async () => {
		try { return parsePresenceReport(await request.text(), Date.now()); }
		catch { error(400, 'Supply only home/away and a valid observedAt timestamp'); }
	})();
	const response = await fetch(`${api.replace(/\/$/, '')}/deployments/${deployment}/create_flow_run`, {
		method: 'POST',
		headers: {
			authorization: `Basic ${Buffer.from(auth).toString('base64')}`,
			'content-type': 'application/json'
		},
		body: JSON.stringify({
			parameters: { report },
			idempotency_key: `presence-${presenceReportKey(report)}`,
			state: { type: 'SCHEDULED' }
		}),
		signal: AbortSignal.timeout(10_000)
	});
	if (!response.ok) error(502, 'Presence report could not be queued');
	return json({ queued: true }, { status: 202, headers: { 'cache-control': 'no-store' } });
};
