import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT } from 'jose';
import { accessVerifier } from '../src/lib/server/access';

const issuer = 'https://test.cloudflareaccess.com';
const audience = 'hub-application';
const { privateKey, publicKey } = await generateKeyPair('RS256');
const keys = createLocalJWKSet({ keys: [{ ...await exportJWK(publicKey), kid: 'test' }] });
const verify = accessVerifier(issuer, audience, keys);
const token = (iss = issuer, aud = audience, expiry = '5m') => new SignJWT({})
    .setProtectedHeader({ alg: 'RS256', kid: 'test' }).setIssuer(iss).setAudience(aud)
    .setSubject('operator').setIssuedAt().setExpirationTime(expiry).sign(privateKey);

test('accepts a signed session for this Access application', async () => {
    assert.equal(await verify(await token()), true);
});

test('rejects missing, expired, wrong-app, wrong-issuer and forged sessions', async () => {
    assert.equal(await verify(null), false);
    assert.equal(await verify('operator:password'), false);
    assert.equal(await verify(await token(issuer, 'another-application')), false);
    assert.equal(await verify(await token('https://other.cloudflareaccess.com')), false);
    assert.equal(await verify(await token(issuer, audience, '-1s')), false);
    const forgedKey = await generateKeyPair('RS256');
    const forged = await new SignJWT({}).setProtectedHeader({ alg: 'RS256', kid: 'test' })
        .setIssuer(issuer).setAudience(audience).setSubject('operator').setIssuedAt()
        .setExpirationTime('5m').sign(forgedKey.privateKey);
    assert.equal(await verify(forged), false);
});
