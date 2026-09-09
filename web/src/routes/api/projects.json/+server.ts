import { json } from '@sveltejs/kit';
import { projectInventory } from '$lib/server/projects';

export function GET() {
  return json(projectInventory, { headers: { 'Cache-Control':'no-cache' } });
}
