import { env } from '$env/dynamic/private';
import type { Handle } from '@sveltejs/kit';
import { accessVerifier } from '$lib/server/access';

const verify = env.CF_ACCESS_ISSUER && env.CF_ACCESS_AUDIENCE
    ? accessVerifier(env.CF_ACCESS_ISSUER, env.CF_ACCESS_AUDIENCE)
    : null;

export const handle: Handle = async ({ event, resolve }) => {
    if (event.url.pathname === '/gardener' || event.url.pathname.startsWith('/gardener/')) {
        if (!verify) return new Response('Gardener access is not configured', {
            status: 503, headers: { 'cache-control': 'no-store' }
        });
        if (!await verify(event.request.headers.get('cf-access-jwt-assertion'))) {
            return new Response('Open Gardener through https://hub.waow.tech/gardener to sign in.', {
                status: 403, headers: { 'cache-control': 'no-store' }
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
