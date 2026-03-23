import { json } from '@sveltejs/kit';
import { loadCards } from '$lib/server/loaders';

export async function GET() {
	return json(await loadCards());
}
