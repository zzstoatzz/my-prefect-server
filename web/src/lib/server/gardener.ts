import { createHash } from 'node:crypto';
import { z } from 'zod';

const stage = z.object({
    started_at: z.number(), finished_at: z.number().nullable(), seconds: z.number().nullable(),
    outcome: z.enum(['running', 'completed', 'failed'])
});
const attempt = z.object({
    vm: z.string(), flow_run_id: z.string().uuid(), run_name: z.string().nullable(),
    image: z.string().nullable(), timeout_seconds: z.number().nullable(),
    created_at: z.number(), updated_at: z.number(),
    phase: z.enum(['creating', 'bootstrapping', 'starting', 'running', 'exited', 'retained', 'deleted', 'missing']),
    exit_code: z.number().nullable(), reason: z.string().nullable(), error: z.string().nullable(),
    stages: z.record(z.string(), stage)
});
const observation = z.object({
    version: z.literal(1), pool: z.string(), worker: z.string(), host: z.string(),
    published_at: z.number(), observed_at: z.number().nullable(), observer_error: z.string().nullable(),
    active_attempts: z.number(), attempts: z.array(attempt)
});
const poolSchema = z.object({
    id: z.string().uuid(), name: z.string(), type: z.literal('exe'),
    status: z.string().nullable(), is_paused: z.boolean(), concurrency_limit: z.number().nullable()
});
const workersSchema = z.array(z.object({
    name: z.string(), status: z.string(), last_heartbeat_time: z.string().nullable()
}));
const deploymentsSchema = z.array(z.object({
    id: z.string().uuid(), name: z.string(), work_pool_name: z.string().nullable(),
    paused: z.boolean(), job_variables: z.object({
        image: z.string().nullable().optional(), timeout_seconds: z.number().optional()
    })
}));
const runsSchema = z.array(z.object({
    id: z.string().uuid(), name: z.string(), deployment_id: z.string().uuid().nullable(),
    state_type: z.string().nullable(), state_name: z.string().nullable(),
    created: z.string(), start_time: z.string().nullable(), end_time: z.string().nullable(),
    infrastructure_pid: z.string().nullable(), run_count: z.number()
}));
const artifactsSchema = z.array(z.object({
    data: z.union([z.string().transform(text => JSON.parse(text)).pipe(z.array(observation)), z.array(observation)])
}));

type PrefectQuery = {
    limit?: number;
    sort?: 'EXPECTED_START_TIME_DESC';
    flow_runs?: { deployment_id: { any_: string[] } };
    artifacts?: { key: { any_: string[] } };
};

export async function loadGardener(api: string, auth: string, request: typeof fetch) {
    const call = async (path: string, body?: PrefectQuery) => {
        const response = await request(`${api.replace(/\/$/, '')}${path}`, {
            method: body ? 'POST' : 'GET',
            headers: { authorization: `Basic ${Buffer.from(auth).toString('base64')}`, 'content-type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
            signal: AbortSignal.timeout(8000)
        });
        if (!response.ok) throw new Error(`Prefect request failed (${response.status})`);
        return response.json();
    };
    const [pool, workers, deployments] = await Promise.all([
        call('/work_pools/gardener-exe').then(value => poolSchema.parse(value)),
        call('/work_pools/gardener-exe/workers/filter', {}).then(value => workersSchema.parse(value)),
        call('/deployments/filter', { limit: 200 }).then(value => deploymentsSchema.parse(value))
    ]);
    const routed = deployments.filter(deployment => deployment.work_pool_name === pool.name);
    const ids = new Set(routed.map(deployment => deployment.id));
    const prefix = `prefect-${createHash('sha256').update(`prefect-pool-${pool.id.replaceAll('-', '')}`).digest('hex').slice(0, 8)}-`;
    const runs = routed.length ? runsSchema.parse(await call('/flow_runs/filter', {
        flow_runs: { deployment_id: { any_: [...ids] } }, limit: 50, sort: 'EXPECTED_START_TIME_DESC'
    })).filter(run => run.deployment_id !== null && ids.has(run.deployment_id) &&
        (!run.infrastructure_pid || run.infrastructure_pid.startsWith(prefix))) : [];
    let snapshot: z.infer<typeof observation> | null = null;
    let observationError: string | null = null;
    try {
        const artifacts = artifactsSchema.parse(await call('/artifacts/filter', {
            artifacts: { key: { any_: [`exe-worker-${pool.id.replaceAll('-', '')}`] } }, limit: 1
        }));
        const data = artifacts[0]?.data;
        if (data !== undefined) {
            snapshot = data[0] ?? null;
        }
    } catch {
        observationError = 'Worker observations could not be read. Flow state below is still available.';
    }
    return { pool, workers, deployments: routed, runs, snapshot, observationError, loadedAt: Date.now() };
}
