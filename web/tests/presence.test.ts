import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { authorizedPresenceReport, parsePresenceReport, presenceReportKey } from '../src/lib/server/presence';

const now = Date.parse('2026-09-05T14:00:00Z');

test('accepts only the scoped bearer credential', () => {
  assert.equal(authorizedPresenceReport('Bearer test-token', 'test-token'), true);
  assert.equal(authorizedPresenceReport('Bearer wrong-token', 'test-token'), false);
  assert.equal(authorizedPresenceReport(null, 'test-token'), false);
  assert.equal(authorizedPresenceReport('Bearer ', ''), false);
});

test('accepts and normalizes a home report without location', () => {
  const result = parsePresenceReport('state=home&observedAt=2026-09-05T12%3A00%3A00Z', now);
  assert.deepEqual(result, { state: 'home', observedAt: '2026-09-05T12:00:00.000Z' });
  assert.equal(presenceReportKey(result), presenceReportKey({ ...result }));
  assert.notEqual(presenceReportKey(result), presenceReportKey({ ...result, state: 'away' }));
});

for (const body of [
  'state=home&observedAt=2026-09-05T12:00:00Z&latitude=41.8',
  'state=home&state=away&observedAt=2026-09-05T12:00:00Z',
  'state=unknown&observedAt=2026-09-05T12:00:00Z',
  'state=home&observedAt=2026-09-05T12:00:00',
  'state=home&observedAt=2026-09-06T12:00:00Z'
]) {
  test(`rejects invalid report: ${body}`, () => {
    assert.throws(() => parsePresenceReport(body, now));
  });
}
