import { json } from '@sveltejs/kit';
import { loadInfraCosts } from '$lib/server/costs';

export async function GET() {
	return json(await loadInfraCosts());
}
