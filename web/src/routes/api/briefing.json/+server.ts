import { json, error } from '@sveltejs/kit';
import { loadBriefing } from '$lib/server/loaders';

export async function GET() {
	const briefing = await loadBriefing();
	if (!briefing) throw error(404, 'no briefing available');
	return json(briefing);
}
