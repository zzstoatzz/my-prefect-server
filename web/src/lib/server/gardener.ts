import { createHash } from 'node:crypto';
import { z } from 'zod';
import { renderMarkdown } from './markdown';
import { isWorking, needsAttention } from '../gardener';

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
    infrastructure_pid: z.string().nullable(), run_count: z.number(),
    parameters: z.object({
        prompt: z.string().optional(), task: z.string().optional(), title: z.string().optional(),
        repo: z.string().nullable().optional(),
        workspace: z.object({ repo: z.string().nullable().optional() }).nullable().optional()
    }).default({}),
    state: z.object({ message: z.string().nullable().optional() }).nullable().optional()

}));
export type Work = z.infer<typeof runsSchema>[number];
export type Attempt = z.infer<typeof attempt>;

const artifactsSchema = z.array(z.object({
    data: z.union([z.string().transform(text => JSON.parse(text)).pipe(z.array(observation)), z.array(observation)])
}));

type PrefectQuery = {
    limit?: number;
    offset?: number;
    sort?: 'EXPECTED_START_TIME_DESC';
    flow_runs?: { deployment_id: { any_: string[] }; state?: { type: { any_: string[] } } };
    artifacts?: { key?: { any_: string[] }; flow_run_id?: { any_: string[] }; type?: { any_: string[] } };
};

export async function loadGardener(api: string, auth: string, request: typeof fetch, selection: { runId?: string | null; filter?: string | null; includeResult?: boolean } = {}) {
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
    const recentRuns = routed.length ? runsSchema.parse(await call('/flow_runs/filter', {
        flow_runs: { deployment_id: { any_: [...ids] } }, limit: 50, sort: 'EXPECTED_START_TIME_DESC'
    })).filter(run => run.deployment_id !== null && ids.has(run.deployment_id) &&
        (!run.infrastructure_pid || run.infrastructure_pid.startsWith(prefix))) : [];
    const activeRuns: z.infer<typeof runsSchema> = [];
    let workInventoryComplete = true;
    if (routed.length) {
        for (let offset = 0; offset < 1000; offset += 100) {
            const batch = runsSchema.parse(await call('/flow_runs/filter', {
                flow_runs: { deployment_id: { any_: [...ids] }, state: { type: { any_: ['SCHEDULED', 'PENDING', 'RUNNING', 'CANCELLING', 'PAUSED'] } } },
                limit: 100, offset
            }));
            activeRuns.push(...batch.filter(run => run.deployment_id !== null && ids.has(run.deployment_id) &&
                (!run.infrastructure_pid || run.infrastructure_pid.startsWith(prefix)) &&
                ['SCHEDULED', 'PENDING', 'RUNNING', 'CANCELLING', 'PAUSED'].includes(run.state_type ?? '')));
            if (batch.length < 100) break;
            if (offset === 900) workInventoryComplete = false;
        }
    }
    const runs = [...new Map([...recentRuns, ...activeRuns].map(run => [run.id, run])).values()]
        .sort((left, right) => Date.parse(right.created) - Date.parse(left.created));
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
    const candidates = runs.filter(run => selection.filter === 'attention'
        ? needsAttention(run, snapshot?.attempts.find(attempt => attempt.vm === run.infrastructure_pid))
        : selection.filter === 'working' ? isWorking(run)
        : selection.filter === 'finished' ? run.state_type === 'COMPLETED' : true);
    const selected = selection.runId ? runs.find(run => run.id === selection.runId) ?? null : candidates[0] ?? null;
    let result: { html: string; id: string } | null = null;
    let resultError: string | null = null;
    if (selected && selection.includeResult !== false) {
        try {
            const artifacts = z.array(z.object({
                id: z.string().uuid(), flow_run_id: z.string().uuid().nullable(),
                key: z.string().nullable(), type: z.string().nullable(), data: z.string().nullable()
            })).parse(await call('/artifacts/filter', {
                artifacts: { flow_run_id: { any_: [selected.id] }, type: { any_: ['markdown'] } }, limit: 30
            }));
            const output = artifacts.find(artifact => artifact.flow_run_id === selected.id &&
                artifact.type === 'markdown' && artifact.key === 'pi-agent-output' && artifact.data);
            if (output?.data) result = { id: output.id, html: renderMarkdown(output.data.slice(0, 100_000)) };
        } catch {
            resultError = 'The saved result could not be loaded. Try refreshing, or open this run in Prefect.';
        }
    }
    return { pool, workers, deployments: routed, runs, workInventoryComplete, selected, result, resultError, snapshot, observationError, loadedAt: Date.now() };
}
