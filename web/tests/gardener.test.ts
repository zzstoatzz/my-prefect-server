import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { test } from 'node:test';
import { loadGardener } from '../src/lib/server/gardener';

const poolId = '7c023916-feb2-477a-8bc6-58e6dda15122';
const deploymentId = '2c0ba8f3-7adf-44f8-a96e-8214c1bd8f2f';
const runId = '8b657b9f-35db-457a-9ec9-234f0be1e481';
const vm = 'prefect-7df0c11a-8b657b9f35db457a9ec9234f0be1e481-r0';
const timestamp = '2026-10-08T06:58:09Z';

for (const brokenObservation of [false, true]) {
    test(`loads actual HTTP responses and isolates observation failure (${brokenObservation})`, async () => {
        const paths: string[] = [];
        const server = createServer(async (request, response) => {
            assert.equal(request.headers.authorization, `Basic ${Buffer.from('reader:test').toString('base64')}`);
            const path = request.url ?? '';
            paths.push(path);
            response.setHeader('content-type', 'application/json');
            let body = '';
            for await (const chunk of request) body += chunk;
            switch (path) {
                case '/work_pools/gardener-exe':
                    response.end(JSON.stringify({ id: poolId, name: 'gardener-exe', type: 'exe', status: 'READY', is_paused: false, concurrency_limit: 2 })); break;
                case '/work_pools/gardener-exe/workers/filter':
                    response.end(JSON.stringify([{ name: 'pi-exe-worker', status: 'ONLINE', last_heartbeat_time: timestamp }])); break;
                case '/deployments/filter':
                    response.end(JSON.stringify([{ id: deploymentId, name: 'investigate', work_pool_name: 'gardener-exe', paused: false, job_variables: {} }])); break;
                case '/flow_runs/filter': {
                    assert.deepEqual(JSON.parse(body).flow_runs.deployment_id.any_, [deploymentId]);
                    const run = { id: runId, name: 'investigate', deployment_id: deploymentId, state_type: 'COMPLETED', state_name: 'Completed', created: timestamp, start_time: timestamp, end_time: timestamp, infrastructure_pid: vm, run_count: 1 };
                    response.end(JSON.stringify([run, { ...run, infrastructure_pid: 'previous-sprites-attempt' }])); break;
                }
                case '/artifacts/filter':
                    assert.deepEqual(JSON.parse(body).artifacts.key.any_, [`exe-worker-${poolId.replaceAll('-', '')}`]);
                    response.end(JSON.stringify([{ data: brokenObservation ? 'not-json' : JSON.stringify([{ version: 1, pool: 'gardener-exe', worker: 'pi-exe-worker', host: 'heavypad', published_at: 1, observed_at: 1, observer_error: null, active_attempts: 0, attempts: [] }]) }])); break;
                default: response.writeHead(404).end();
            }
        });
        await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
        try {
            const address = server.address();
            assert(address && typeof address !== 'string');
            const result = await loadGardener(`http://127.0.0.1:${address.port}`, 'reader:test', fetch);
            assert.equal(result.runs.length, 1);
            assert.equal(result.runs[0].infrastructure_pid, vm);
            assert.equal(result.pool.concurrency_limit, 2);
            assert.equal(result.snapshot === null, brokenObservation);
            assert.equal(result.observationError !== null, brokenObservation);
            assert.equal(paths.length, 5);
            assert(!JSON.stringify(result).includes('reader:test'));
        } finally {
            server.closeAllConnections();
            await new Promise<void>(resolve => server.close(() => resolve()));
        }
    });
}
