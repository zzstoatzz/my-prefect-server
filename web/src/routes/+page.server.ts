import { loadCards, loadStats, loadBriefing, loadSpendSummary } from '$lib/server/loaders';
import { loadInfraCosts } from '$lib/server/costs';

export async function load() {
	const [stats, cards, briefing, spend, infraCosts] = await Promise.all([
		loadStats(),
		loadCards(),
		loadBriefing(),
		loadSpendSummary(),
		loadInfraCosts()
	]);

	return { stats, cards, briefing, spend, infraCosts };
}
