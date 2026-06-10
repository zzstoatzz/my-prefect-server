import { loadCards, loadStats, loadBriefing, loadSpendSummary } from '$lib/server/loaders';

export async function load() {
	const [stats, cards, briefing, spend] = await Promise.all([
		loadStats(),
		loadCards(),
		loadBriefing(),
		loadSpendSummary()
	]);

	return { stats, cards, briefing, spend };
}
