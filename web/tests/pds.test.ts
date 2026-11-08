import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { createPdsResolver, didDocumentUrl, pdsEndpoint } from '../src/lib/server/pds';

const did = 'did:plc:xbtmt2zjwlrfegqvch7fboei';

function document(service: Array<{ id: string; type: string; serviceEndpoint: string }>) {
	return { id: did, alsoKnownAs: ['at://zzstoatzz.io'], service };
}

const pds = { id: '#atproto_pds', type: 'AtprotoPersonalDataServer', serviceEndpoint: 'https://pds.test/' };

test('locates the DID document for did:plc and host-level did:web', () => {
	assert.equal(didDocumentUrl(did), `https://plc.directory/${did}`);
	assert.equal(didDocumentUrl('did:web:example.test'), 'https://example.test/.well-known/did.json');
	assert.equal(didDocumentUrl('did:web:localhost%3A3000'), 'https://localhost:3000/.well-known/did.json');
	assert.throws(() => didDocumentUrl('did:web:example.test:user'));
	assert.throws(() => didDocumentUrl('zzstoatzz.io'));
});

test('reads the atproto_pds service endpoint, with or without the DID prefix', () => {
	const other = { id: '#other', type: 'Other', serviceEndpoint: 'https://other.test' };
	assert.equal(pdsEndpoint(did, document([other, pds])), 'https://pds.test');
	assert.equal(pdsEndpoint(did, document([{ ...pds, id: `${did}#atproto_pds` }])), 'https://pds.test');
	assert.throws(() => pdsEndpoint(did, document([other])), /no #atproto_pds/);
	assert.throws(() => pdsEndpoint(did, { ...document([pds]), id: 'did:plc:someoneelse' }));
});

test('resolves once per DID and retries after a failure', async () => {
	const requested: string[] = [];
	let status = 503;
	const resolve = createPdsResolver(async (input) => {
		requested.push(String(input));
		return Response.json(document([pds]), { status });
	});
	await assert.rejects(resolve(did), /503/);
	status = 200;
	assert.equal(await resolve(did), 'https://pds.test');
	assert.equal(await resolve(did), 'https://pds.test');
	assert.deepEqual(requested, [`https://plc.directory/${did}`, `https://plc.directory/${did}`]);
});
