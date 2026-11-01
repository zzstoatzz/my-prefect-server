import { json } from '@sveltejs/kit';
import { loadStats } from '$lib/server/loaders';

export async function GET() {
	return json(await loadStats());
}
