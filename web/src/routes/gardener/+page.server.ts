import { env } from '$env/dynamic/private';
import { loadGardener } from '$lib/server/gardener';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
    const api = env.PREFECT_API_URL;
    const auth = env.GARDENER_API_AUTH_STRING || env.PREFECT_API_AUTH_STRING;
    if (!api || !auth) return { gardener: null, unavailable: 'Prefect access is not configured.' };
    try {
        return { gardener: await loadGardener(api, auth, fetch), unavailable: null };
    } catch {
        return { gardener: null, unavailable: 'Prefect could not be reached or returned an unexpected response. Refresh to retry.' };
    }
};
