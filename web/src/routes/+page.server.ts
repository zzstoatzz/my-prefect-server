import { loadCards, loadStats, loadBriefing } from '$lib/server/loaders';

export async function load() {
	const [stats, cards, briefing] = await Promise.all([
		loadStats(),
		loadCards(),
		loadBriefing()
	]);

	return { stats, cards, briefing };
}
