import { json } from '@sveltejs/kit';
import { loadFleetStatus } from '$lib/server/projects';

export async function GET() {
  try {
    return json(await loadFleetStatus(), {headers:{'Cache-Control':'no-store'}});
  } catch (error) {
    console.warn('Fleet report unavailable', error);
    return json({ error:'Fleet report unavailable, incomplete, or older than 30 minutes' }, {status:503});
  }
}
