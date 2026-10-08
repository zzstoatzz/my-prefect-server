import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey } from 'jose';

export function accessVerifier(
    issuer: string,
    audience: string,
    keys: JWTVerifyGetKey = createRemoteJWKSet(new URL('/cdn-cgi/access/certs', issuer))
) {
    return async (token: string | null): Promise<boolean> => {
        if (!token) return false;
        try {
            await jwtVerify(token, keys, {
                issuer, audience, algorithms: ['RS256'], requiredClaims: ['exp', 'iat', 'sub']
            });
            return true;
        } catch {
            return false;
        }
    };
}
