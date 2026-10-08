<script lang="ts">
    import { onMount } from 'svelte';
    import { invalidateAll } from '$app/navigation';
    import type { PageData } from './$types';

    let { data }: { data: PageData } = $props();
    let selected = $state<string | null>(null);
    let refreshing = $state(false);
    let now = $state(Date.now());
    const time = (value: number | null) => value === null ? '—' : new Intl.DateTimeFormat('en-US', {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC'
    }).format(new Date(value * 1000));
    const duration = (seconds: number | null) => seconds === null ? '—' : seconds < 60 ? `${seconds.toFixed(1)}s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    const label = (value: string) => value.replaceAll('_', ' ').toLowerCase();
    const flowUrl = (id: string) => `https://prefect-server.waow.tech/runs/flow-run/${id}`;
    const stageNames = { create: 'Create VM', bootstrap: 'Prepare environment', service_start: 'Start flow', artifact_delivery: 'Save diagnostics', delete: 'Delete VM' };
    let model = $derived(data.gardener);
    let snapshot = $derived(model?.snapshot);
    let stale = $derived(!snapshot?.observed_at || now - snapshot.observed_at * 1000 > 90_000);
    let attempts = $derived(snapshot?.attempts ?? []);
    let active = $derived(attempts.filter(attempt => !['deleted', 'missing'].includes(attempt.phase)));
    let attention = $derived(active.filter(attempt => attempt.error || now / 1000 - attempt.created_at > (attempt.timeout_seconds ?? 2400) + 600));
    let chosen = $derived(attempts.find(attempt => attempt.vm === selected) ?? attempts[0]);
    let online = $derived(model?.workers.filter(worker => worker.status === 'ONLINE' && worker.last_heartbeat_time && now - Date.parse(worker.last_heartbeat_time) < 90_000) ?? []);

    async function refresh() {
        refreshing = true;
        try { await invalidateAll(); now = Date.now(); }
        finally { refreshing = false; }
    }
    onMount(() => {
        const interval = setInterval(() => {
            now = Date.now();
            if (!document.hidden && !refreshing) void refresh().catch(() => {});
        }, 30_000);
        return () => clearInterval(interval);
    });
</script>

<svelte:head><title>Gardener · hub</title><meta name="robots" content="noindex,nofollow" /></svelte:head>

<div class="gardener">
    <header class="heading">
        <div><h1>Gardener</h1><p>Agent work, from request to a clean machine.</p></div>
        <button class="refresh" onclick={refresh} disabled={refreshing}>{refreshing ? 'Refreshing…' : 'Refresh'}</button>
    </header>

    {#if data.unavailable}
        <div class="notice" role="alert"><h2>Connection unavailable</h2><p>{data.unavailable}</p></div>
    {:else if model}
        <div class="summary" aria-label="Worker status">
            <span class:good={online.length > 0} class:warn={online.length === 0}>{online.length ? `${online.length} worker online` : 'No worker heartbeat'}</span>
            <span>{model.pool.is_paused ? 'Pool paused' : 'Pool accepting work'}</span>
            <span>{model.pool.concurrency_limit === null ? 'Concurrency not capped' : `Concurrency limit ${model.pool.concurrency_limit}`}</span>
            <span class="timestamp">Checked {time(model.loadedAt / 1000)} UTC</span>
        </div>

        <section class="route" aria-label="How work runs">
            <div><span class="step">01</span><h2>Request</h2><p>Phi or an automation</p></div>
            <div><span class="step">02</span><h2>Queue</h2><p>Prefect · {model.pool.name}</p></div>
            <div><span class="step">03</span><h2>Dispatch</h2><p>{snapshot?.host ?? 'heavypad'} · exe worker</p></div>
            <div><span class="step">04</span><h2>Execute</h2><p>Fresh VM · isolated Pi</p></div>
            <div><span class="step">05</span><h2>Clean up</h2><p>Save diagnostics · delete VM</p></div>
        </section>

        <div class="boundary"><span>Inference</span> Pi calls the grant gateway on heavypad. Each attempt has a model restriction, expiry, and 32-request limit.</div>

        {#if model.observationError}
            <div class="notice" role="status">{model.observationError}</div>
        {:else if !snapshot}
            <div class="notice" role="status">VM observations have not been published yet. Prefect runs are available below; VM cleanup is not yet observable here.</div>
        {:else if stale || snapshot.observer_error}
            <div class="notice" role="status"><strong>VM observations are {stale ? 'stale' : 'degraded'}.</strong> Last successful observation: {time(snapshot.observed_at)} UTC. {snapshot.observer_error ?? ''} A worker heartbeat does not establish that VM reconciliation is working.</div>
        {/if}

        {#if attention.length}
            <section class="attention"><h2>Needs attention</h2>
                {#each attention as attempt (attempt.vm)}
                    <button onclick={() => selected = attempt.vm}><strong>{attempt.run_name ?? attempt.vm}</strong><span>{attempt.error ?? 'VM has outlived its execution budget and cleanup grace period'}</span></button>
                {/each}
            </section>
        {/if}

        <section class="runs">
            <div class="section-heading"><h2>Recent work</h2><span>{model.runs.length} runs · newest scheduled first</span></div>
            {#if model.runs.length === 0}
                <p class="empty">No runs for the deployments currently assigned to this pool.</p>
            {:else}
                <div class="run-head" aria-hidden="true"><span>Run</span><span>Flow state</span><span>VM lifecycle</span><span>Execution</span></div>
                {#each model.runs as run (run.id)}
                    {@const observed = attempts.find(attempt => attempt.vm === run.infrastructure_pid)}
                    <div class="run-row">
                        <div class="run-name"><a href={flowUrl(run.id)} target="_blank" rel="noreferrer">{run.name}</a><small>{model.deployments.find(deployment => deployment.id === run.deployment_id)?.name ?? 'Deployment unavailable'} · {time(Date.parse(run.start_time ?? run.created) / 1000)} UTC</small></div>
                        <span class="state" class:good={run.state_type === 'COMPLETED'} class:warn={['FAILED', 'CRASHED'].includes(run.state_type ?? '')}>{label(run.state_name ?? run.state_type ?? 'unknown')}</span>
                        <div>{#if observed}<button class="text-button" onclick={() => selected = observed.vm}>{label(observed.phase)}{observed.error ? ' · attention' : ''}</button>{:else}<span class="muted">{run.infrastructure_pid ? 'Not observed' : 'Not assigned'}</span>{/if}</div>
                        <span class="elapsed">{run.start_time && run.end_time ? duration((Date.parse(run.end_time) - Date.parse(run.start_time)) / 1000) : run.state_type === 'RUNNING' && run.start_time ? duration((now - Date.parse(run.start_time)) / 1000) : '—'}</span>
                    </div>
                {/each}
            {/if}
        </section>

        {#if chosen}
            <section class="attempt">
                <div class="section-heading"><h2>VM attempt</h2><span>{stale ? 'Last known state' : 'Observed state'}</span></div>
                <label class="select-label" for="attempt">Inspect attempt</label>
                <select id="attempt" value={chosen.vm} onchange={event => selected = event.currentTarget.value}>
                    {#each attempts as attempt (attempt.vm)}<option value={attempt.vm}>{attempt.run_name ?? attempt.vm} — {attempt.phase}</option>{/each}
                </select>
                <div class="attempt-facts"><span>{chosen.vm}</span><a href={flowUrl(chosen.flow_run_id)} target="_blank" rel="noreferrer">Open flow in Prefect</a></div>
                <div class="stages">
                    {#each Object.entries(stageNames) as [key, title]}
                        {@const stage = chosen.stages[key]}
                        <div class:finished={stage?.outcome === 'completed'} class:failed={stage?.outcome === 'failed'}>
                            <span>{title}</span><strong>{stage ? stage.outcome === 'running' ? 'In progress' : duration(stage.seconds) : 'Not recorded'}</strong>
                            <small>{stage ? label(stage.outcome) : '—'}</small>
                        </div>
                    {/each}
                </div>
                <dl><div><dt>Image</dt><dd>{chosen.image ?? 'Provider default image'}</dd></div><div><dt>Execution budget</dt><dd>{duration(chosen.timeout_seconds)}</dd></div><div><dt>Process outcome</dt><dd>{chosen.reason ?? 'Not observed'}{chosen.exit_code !== null ? ` (exit ${chosen.exit_code})` : ''}</dd></div></dl>
                {#if chosen.error}<p class="warn" role="status">{chosen.error}</p>{/if}
            </section>
        {/if}

        <section class="deployments"><div class="section-heading"><h2>Runs here</h2><span>{model.deployments.length} deployments</span></div>
            {#each model.deployments as deployment (deployment.id)}
                <div class="deployment"><a href={`https://prefect-server.waow.tech/deployments/deployment/${deployment.id}`} target="_blank" rel="noreferrer">{deployment.name}</a><span>{deployment.job_variables.image ?? 'Provider default image'}</span><span>{duration(deployment.job_variables.timeout_seconds ?? null)} budget</span></div>
            {/each}
        </section>
        <footer>Flow state comes from Prefect. VM state comes from worker observations. Refreshes every 30 seconds while this page is visible.{#if snapshot} Showing {attempts.length} recorded attempts; {snapshot.active_attempts} have no confirmed cleanup.{/if}</footer>
    {/if}
</div>

<style>
    .gardener { padding: 2.5rem 1.5rem 4rem; color: #e5e7eb; }
    .heading, .section-heading { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
    h1 { font-size: 2rem; font-weight: 500; letter-spacing: -.04em; }
    h2 { font-size: 1rem; font-weight: 500; }
    .heading p { color: #9ca3af; margin-top: .35rem; }
    button, select { font: inherit; }
    button:focus-visible, select:focus-visible, a:focus-visible { outline: 2px solid #93c5fd; outline-offset: 4px; }
    .refresh { border: 1px solid #4b5563; border-radius: .5rem; padding: .55rem 1rem; }
    .refresh:disabled { opacity: .6; }
    .summary { display: flex; flex-wrap: wrap; gap: .7rem 1.5rem; font-size: .8rem; padding: 1.5rem 0; color: #aeb6c4; }
    .timestamp { margin-left: auto; }
    .good { color: #86cbaa; } .warn { color: #f3bc83; } .muted { color: #9ca3af; }
    .route { display: grid; grid-template-columns: repeat(5, 1fr); border-block: 1px solid #374151; padding: 1.5rem 0; }
    .route > div { padding: 0 1rem; border-left: 1px solid #374151; }
    .route > div:first-child { padding-left: 0; border: 0; }
    .step { display: block; color: #93c5fd; font-size: .75rem; margin-bottom: .6rem; }
    .route p { font-size: .8rem; color: #aeb6c4; margin-top: .3rem; }
    .boundary { font-size: .8rem; color: #aeb6c4; padding: 1rem 0; }
    .boundary span { color: #d1d5db; margin-right: .6rem; }
    .notice { border-left: 3px solid #d2a573; background: #20242b; padding: 1rem; margin: 1rem 0; font-size: .9rem; line-height: 1.6; }
    .attention { margin: 1.5rem 0; }
    .attention button { display: flex; flex-wrap: wrap; gap: .5rem 1rem; text-align: left; padding: .8rem 0; color: #f3bc83; }
    .section-heading { margin: 2rem 0 1rem; }
    .section-heading > span { font-size: .75rem; color: #9ca3af; }
    .run-head, .run-row { display: grid; grid-template-columns: minmax(0, 2fr) 1fr 1fr .65fr; gap: 1rem; align-items: center; }
    .run-head { font-size: .75rem; color: #9ca3af; padding-bottom: .75rem; }
    .run-row { border-top: 1px solid #263141; padding: 1rem 0; font-size: .85rem; }
    a, .text-button { color: #b1cff6; text-decoration: none; }
    a:hover, .text-button:hover { text-decoration: underline; }
    .text-button { text-align: left; }
    .run-name { min-width: 0; overflow-wrap: anywhere; }
    small { display: block; font-size: .75rem; color: #9ca3af; margin-top: .3rem; }
    .elapsed { font-variant-numeric: tabular-nums; }
    .select-label { display: block; color: #9ca3af; font-size: .8rem; margin-bottom: .5rem; }
    select { max-width: 100%; background: #172131; color: #e5e7eb; padding: .65rem; border: 1px solid #4b5563; border-radius: .4rem; }
    .attempt-facts { display: flex; flex-wrap: wrap; gap: .5rem 1.5rem; overflow-wrap: anywhere; margin: 1rem 0; color: #9ca3af; font-size: .75rem; }
    .stages { display: grid; grid-template-columns: repeat(5, 1fr); gap: .75rem; }
    .stages > div { border-top: 3px solid #374151; padding-top: .75rem; font-size: .8rem; }
    .stages .finished { border-color: #609c81; } .stages .failed { border-color: #d2a573; }
    .stages strong { display: block; font-size: 1.15rem; font-weight: 400; margin-top: .65rem; font-variant-numeric: tabular-nums; }
    dl { margin-top: 1.5rem; font-size: .8rem; }
    dl > div { display: grid; grid-template-columns: 9rem minmax(0, 1fr); padding: .35rem 0; gap: 1rem; }
    dt { color: #9ca3af; } dd { overflow-wrap: anywhere; }
    .deployment { display: grid; grid-template-columns: 1fr 2fr 1fr; gap: 1rem; border-top: 1px solid #263141; padding: .8rem 0; font-size: .85rem; overflow-wrap: anywhere; }
    .deployment span { color: #9ca3af; }
    footer, .empty { color: #9ca3af; font-size: .8rem; line-height: 1.6; margin-top: 2rem; }
    @media (max-width: 700px) {
        .gardener { padding: 1.5rem 1rem 3rem; }
        .heading { align-items: flex-start; }
        .timestamp { margin-left: 0; flex-basis: 100%; }
        .route { grid-template-columns: 1fr; gap: 1rem; }
        .route > div, .route > div:first-child { border-left: 2px solid #374151; padding-left: 1rem; display: grid; grid-template-columns: 1.5rem 5.5rem 1fr; align-items: baseline; gap: .5rem; }
        .step, .route p { margin: 0; }
        .run-head { display: none; }
        .run-row { grid-template-columns: 1fr 1fr auto; gap: .6rem; }
        .run-name { grid-column: 1 / -1; }
        .stages { grid-template-columns: repeat(2, 1fr); gap: 1rem; }
        .deployment { grid-template-columns: 1fr auto; }
        .deployment span:first-of-type { grid-column: 1 / -1; grid-row: 2; }
        .section-heading { align-items: baseline; }
        dl > div { grid-template-columns: 7rem minmax(0, 1fr); }
    }
</style>
