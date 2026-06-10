import { json } from '@sveltejs/kit';
import { loadSpendSummary } from '$lib/server/loaders';

export async function GET() {
	return json(await loadSpendSummary());
}
