<script lang="ts">
    import { duration, isWorking, repository, requestText, workStatus, workTitle, type Gardener, type Work, type Attempt } from '$lib/gardener';
    let { run, result, resultError, attempt, stale, formatDate } : {
        run: Work; result: Gardener['result']; resultError: string | null; attempt?: Attempt;
        stale: boolean; formatDate: (value: string) => string;
    } = $props();
    let copied = $state(false);
    const stages = { create: 'Create machine', bootstrap: 'Prepare environment', service_start: 'Start work', artifact_delivery: 'Save diagnostics', delete: 'Remove machine' };
    const flowUrl = (id: string) => `https://prefect-server.waow.tech/runs/flow-run/${id}`;
    async function copyRequest() {
        try { await navigator.clipboard.writeText(requestText(run)); copied = true; }
        catch { copied = false; }
    }
</script>

<article class="reader" aria-label="Selected work">
    <header>
        <div class="reader-meta"><span class="status" class:bad={['FAILED', 'CRASHED'].includes(run.state_type ?? '')} class:good={run.state_type === 'COMPLETED'}>{workStatus(run)}</span><span>{repository(run) ?? 'No repository'}</span></div>
        <h2>{workTitle(run)}</h2>
        <p class="date">Requested {formatDate(run.created)}{#if run.start_time && run.end_time} · {duration((Date.parse(run.end_time) - Date.parse(run.start_time)) / 1000)} execution{/if}</p>
    </header>

    {#if resultError}
        <div class="message problem"><h3>Result unavailable</h3><p>{resultError}</p></div>
    {:else if ['FAILED', 'CRASHED'].includes(run.state_type ?? '')}
        <div class="message problem">
            <h3>{run.state?.message?.includes('prompt rejected') ? 'The request was stopped by the prompt check' : 'This request did not finish'}</h3>
            <p>{run.state?.message?.includes('prompt rejected') ? 'No answer was produced. The policy check’s explanation is below.' : 'Open the run in Prefect to inspect the failure and decide whether to retry.'}</p>
            {#if run.state?.message}<details><summary>Read the failure explanation</summary><p class="failure-text">{run.state.message}</p></details>{/if}
            <a class="action-link" href={flowUrl(run.id)} target="_blank" rel="noreferrer">Inspect run in Prefect ↗</a>
        </div>
    {:else if isWorking(run)}
        <div class="message"><h3>{run.state_type === 'RUNNING' ? 'Work is in progress' : 'Waiting for a result'}</h3><p>The saved answer will appear here when it is available. You can leave this page while the work continues.</p></div>
    {:else if run.state_type === 'PAUSED'}
        <div class="message problem"><h3>A decision is needed</h3><p>{run.state?.message ?? 'This run is paused. Review its request in Prefect before resuming.'}</p><a class="action-link" href={flowUrl(run.id)} target="_blank" rel="noreferrer">Review in Prefect ↗</a></div>
    {/if}

    {#if result}
        <section class="answer"><h3>Result</h3><div class="prose">{@html result.html}</div></section>
    {:else if !resultError && run.state_type === 'COMPLETED'}
        <div class="message"><h3>Work finished without a saved answer</h3><p>{run.state?.message ?? 'No investigation result was published for this run. Its logs and other artifacts are available in Prefect.'}</p><a class="action-link" href={flowUrl(run.id)} target="_blank" rel="noreferrer">See run artifacts ↗</a></div>
    {/if}

    <details class="request" open={!result}>
        <summary>Original request</summary>
        <p>{requestText(run) || 'No request text was recorded for this workflow.'}</p>
        {#if requestText(run)}<button class="quiet-button" onclick={copyRequest}>{copied ? 'Copied' : 'Copy request'}</button>{/if}
    </details>

    <details class="execution">
        <summary><span>Execution details</span><span class="detail-hint">{attempt?.phase === 'deleted' ? 'Machine removed' : attempt ? `${stale ? 'Last known: ' : ''}${attempt.phase}` : 'No machine history'}</span></summary>
        <p class="detail-intro">The flow outcome above and machine cleanup are tracked separately.{stale ? ' These observations may be out of date.' : ''}</p>
        {#if attempt}
            <ol class="stage-list">
                {#each Object.entries(stages) as [key, title]}
                    {@const stage = attempt.stages[key]}
                    <li><span>{title}</span><span>{stage?.outcome === 'running' ? 'In progress' : stage?.outcome === 'failed' ? 'Failed' : stage ? duration(stage.seconds) : 'Not recorded'}</span></li>
                {/each}
            </ol>
            {#if attempt.error}<p class="failure-text">{attempt.error}</p>{/if}
            <dl><dt>Machine</dt><dd>{attempt.vm}</dd><dt>Environment</dt><dd>{attempt.image ?? 'exe.dev default image'}</dd><dt>Time limit</dt><dd>{duration(attempt.timeout_seconds)}</dd></dl>
        {:else}<p>This run has no recorded machine observations. Its absence here does not confirm cleanup.</p>{/if}
        <a class="action-link" href={flowUrl(run.id)} target="_blank" rel="noreferrer">Full logs and controls in Prefect ↗</a>
    </details>
</article>

<style>
    .reader { max-width: 850px; margin: 0 auto; padding: 32px 40px 56px; }
    .reader-meta { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; font-size: 15px; color: var(--muted); }
    h2 { font-size: clamp(24px, 2.4vw, 32px); font-weight: 650; line-height: 1.25; letter-spacing: -.025em; margin: 20px 0 16px; overflow-wrap: anywhere; }
    h3 { font-weight: 650; font-size: 20px; margin-bottom: 12px; }
    .date { font-size: 15px; color: var(--muted); }
    .status { color: var(--blue); font-weight: 600; background: var(--selection); padding: 5px 10px; border-radius: 6px; }
    .bad { color: var(--red); background: var(--red-wash); } .good { color: var(--green); background: var(--green-wash); }
    .message { margin-top: 28px; padding: 22px; background: var(--wash); border-left: 4px solid var(--blue); border-radius: 0 8px 8px 0; }
    .message p { line-height: 1.6; } .problem { border-color: var(--red); }
    .message details { margin-top: 16px; } .failure-text { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 16px; }
    .action-link { display: inline-block; color: var(--blue); font-weight: 600; text-decoration: underline; text-underline-offset: 4px; margin-top: 18px; }
    .answer { margin-top: 36px; }
    .answer > h3 { border-bottom: 1px solid var(--line); padding-bottom: 14px; margin-bottom: 22px; }
    .prose { font-size: 18px; line-height: 1.75; overflow-wrap: anywhere; }
    .prose :global(p), .prose :global(ul), .prose :global(ol), .prose :global(pre), .prose :global(blockquote) { margin: 0 0 1.1em; }
    .prose :global(h1), .prose :global(h2) { font-size: 24px; line-height: 1.35; font-weight: 650; margin: 1.5em 0 .6em; }
    .prose :global(h3), .prose :global(h4) { font-size: 20px; line-height: 1.4; font-weight: 650; margin: 1.5em 0 .5em; }
    .prose :global(ul) { list-style: disc; padding-left: 1.4em; } .prose :global(ol) { list-style: decimal; padding-left: 1.4em; }
    .prose :global(li) { margin-bottom: .5em; }
    .prose :global(a) { color: var(--blue); text-decoration: underline; text-underline-offset: 3px; }
    .prose :global(code) { font-size: .88em; background: var(--wash); padding: .1em .25em; border-radius: 3px; }
    .prose :global(pre) { background: var(--wash); padding: 18px; border-radius: 8px; overflow-x: auto; font-size: 15px; }
    .prose :global(pre code) { padding: 0; font-size: inherit; }
    .prose :global(table) { display: block; overflow-x: auto; border-collapse: collapse; font-size: 16px; margin: 20px 0; }
    .prose :global(th), .prose :global(td) { padding: 12px; text-align: left; border: 1px solid var(--line); min-width: 140px; }
    .prose :global(th) { background: var(--wash); }
    .prose :global(blockquote) { border-left: 3px solid var(--line); padding-left: 20px; }
    .request, .execution { margin-top: 32px; border-top: 1px solid var(--line); padding-top: 22px; }
    summary { cursor: pointer; font-weight: 600; padding: 4px 0; min-height: 32px; }
    .request p { margin-top: 18px; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.65; }
    .quiet-button { border: 1px solid var(--line); border-radius: 7px; padding: 10px 14px; margin-top: 16px; color: var(--blue); background: var(--surface); min-height: 44px; }
    .detail-hint { float: right; font-size: 14px; color: var(--muted); font-weight: 400; }
    .detail-intro { margin: 18px 0; color: var(--muted); line-height: 1.6; }
    .stage-list li { display: flex; justify-content: space-between; gap: 20px; padding: 12px 0; border-bottom: 1px solid var(--line); }
    .stage-list li span:last-child { color: var(--muted); }
    dl { margin-top: 24px; font-size: 15px; } dt { font-weight: 600; margin-top: 16px; } dd { color: var(--muted); overflow-wrap: anywhere; margin-top: 4px; }
    @media (max-width: 760px) { .reader { padding: 24px 20px 40px; } .detail-hint { display: block; float: none; margin: 6px 0 0 18px; } }
</style>
