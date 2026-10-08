import { createHash, timingSafeEqual } from 'node:crypto';
import { env } from '$env/dynamic/private';
import type { Handle } from '@sveltejs/kit';

export const handle: Handle = async ({ event, resolve }) => {
    if (event.url.pathname === '/gardener' || event.url.pathname.startsWith('/gardener/')) {
        const auth = env.GARDENER_VIEW_AUTH_STRING || env.PREFECT_API_AUTH_STRING;
        if (!auth) return new Response('Gardener access is not configured', { status: 503 });
        const digest = (value: string) => createHash('sha256').update(value).digest();
        const expected = `Basic ${Buffer.from(auth).toString('base64')}`;
        if (!timingSafeEqual(digest(event.request.headers.get('authorization') || ''), digest(expected))) {
            return new Response('Sign in with the Gardener viewing credential', {
                status: 401, headers: { 'www-authenticate': 'Basic realm="Gardener", charset="UTF-8"', 'cache-control': 'no-store' }
            });
        }
        const response = await resolve(event);
        response.headers.set('cache-control', 'private, no-store');
        response.headers.set('x-robots-tag', 'noindex, nofollow');
        response.headers.set('referrer-policy', 'same-origin');
        return response;
    }
    return resolve(event);
};
