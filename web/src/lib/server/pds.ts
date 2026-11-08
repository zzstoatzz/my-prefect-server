import { z } from 'zod';

const didDocument = z.object({
	id: z.string(),
	service: z.array(z.object({ id: z.string(), serviceEndpoint: z.unknown() })).optional()
});

export type DidDocument = z.infer<typeof didDocument>;

export function didDocumentUrl(did: string): string {
	if (did.startsWith('did:plc:')) return `https://plc.directory/${did}`;
	const host = did.match(/^did:web:([^:]+)$/)?.[1];
	if (host) return `https://${decodeURIComponent(host)}/.well-known/did.json`;
	throw new Error(`cannot resolve ${did}: expected did:plc or host-level did:web`);
}

export function pdsEndpoint(did: string, doc: DidDocument): string {
	if (doc.id !== did) throw new Error(`DID document for ${did} names ${doc.id}`);
	const service = doc.service?.find((entry) => entry.id.endsWith('#atproto_pds'));
	if (!service) throw new Error(`${did}: DID document has no #atproto_pds service`);
	return z.url().parse(service.serviceEndpoint).replace(/\/+$/, '');
}

export function createPdsResolver(request: typeof fetch): (did: string) => Promise<string> {
	const resolved = new Map<string, Promise<string>>();
	return (did) => {
		let pending = resolved.get(did);
		if (!pending) {
			pending = (async () => {
				const resp = await request(didDocumentUrl(did));
				if (!resp.ok) throw new Error(`DID document for ${did}: ${resp.status}`);
				return pdsEndpoint(did, didDocument.parse(await resp.json()));
			})();
			pending.catch(() => resolved.delete(did));
			resolved.set(did, pending);
		}
		return pending;
	};
}

export const resolvePds = createPdsResolver(fetch);
