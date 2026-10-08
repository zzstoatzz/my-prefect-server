<script lang="ts">
    import { onMount } from 'svelte';
    import { goto, invalidateAll } from '$app/navigation';
    import { page } from '$app/state';
    import WorkReader from '$lib/components/gardener/WorkReader.svelte';
    import SystemMap from '$lib/components/gardener/SystemMap.svelte';
    import { isWorking, needsAttention, repository, requestText, workStatus, workTitle, type WorkFilter } from '$lib/gardener';
    import type { PageData } from './$types';

    let { data }: { data: PageData } = $props();
    let filter = $state<WorkFilter>('all');
    $effect(() => {
        const value = page.url.searchParams.get('filter');
        filter = value === 'attention' || value === 'working' || value === 'finished' ? value : 'all';
    });
    let search = $state('');
    let refreshing = $state(false);
    let refreshFailed = $state(false);
    let now = $state(Date.now());
    let zone = $state('UTC');
    let theme = $state<'light' | 'dark'>('light');
    let model = $derived(data.gardener);
    let system = $derived(page.url.searchParams.get('view') !== 'work');
    let showReader = $derived(page.url.searchParams.has('run'));
    let attempts = $derived(model?.snapshot?.attempts ?? []);
    let stale = $derived(!model?.snapshot?.observed_at || now - model.snapshot.observed_at * 1000 > 90_000);
    let selected = $derived(model?.selected);
    let attempt = $derived(attempts.find(item => item.vm === selected?.infrastructure_pid));
    let counts = $derived({
        all: model?.runs.length ?? 0,
        attention: model?.runs.filter(run => needsAttention(run, attempts.find(item => item.vm === run.infrastructure_pid))).length ?? 0,
        working: model?.runs.filter(isWorking).length ?? 0,
        finished: model?.runs.filter(run => run.state_type === 'COMPLETED').length ?? 0
    });
    const filters: { value: WorkFilter; label: string }[] = [
        { value: 'all', label: 'All work' }, { value: 'attention', label: 'Needs attention' },
        { value: 'working', label: 'Working' }, { value: 'finished', label: 'Finished' }
    ];
    let runs = $derived((model?.runs ?? []).filter(run => {
        const matches = `${requestText(run)} ${run.name} ${repository(run) ?? ''}`.toLowerCase().includes(search.toLowerCase());
        return matches && (filter === 'all' || filter === 'attention' && needsAttention(run, attempts.find(item => item.vm === run.infrastructure_pid)) || filter === 'working' && isWorking(run) || filter === 'finished' && run.state_type === 'COMPLETED');
    }));
    const formatDate = (value: string) => new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: zone }).format(new Date(value));
    const formatShortDate = (value: string) => new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: zone }).format(new Date(value));
    async function refresh() {
        refreshing = true;
        try { await invalidateAll(); now = Date.now(); refreshFailed = false; }
        catch { refreshFailed = true; }
        finally { refreshing = false; }
    }
    async function openRun(id: string) {
        await goto(`?view=work&run=${id}`, { keepFocus: true });
    }
    function toggleTheme() {
        theme = theme === 'light' ? 'dark' : 'light';
        localStorage.setItem('gardener-theme', theme);
    }
    onMount(() => {
        zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        theme = localStorage.getItem('gardener-theme') === 'dark' ? 'dark' : 'light';
        const timer = setInterval(() => {
            now = Date.now();
            if (!document.hidden && !refreshing) void refresh();
        }, 30_000);
        return () => clearInterval(timer);
    });
</script>

<svelte:head><title>Gardener — delegated work</title><meta name="robots" content="noindex,nofollow" /></svelte:head>

<div class="gardener" class:dark={theme === 'dark'}>
    <header class="app-header">
        <div class="identity"><a class="brand" href="/gardener">Gardener</a><span>Execution map</span></div>
        <div class="utilities"><a href="/">Hub</a><button onclick={toggleTheme} aria-label={theme === 'light' ? 'Switch to dark appearance' : 'Switch to light appearance'}>{theme === 'light' ? 'Dark' : 'Light'}</button><button class="refresh" onclick={refresh} disabled={refreshing}>{refreshing ? 'Refreshing…' : 'Refresh'}</button></div>
    </header>
    <nav class="views" aria-label="Gardener"><a href="/gardener" aria-current={system ? 'page' : undefined}>Overview</a><a href="?view=work" aria-current={!system ? 'page' : undefined}>Work history</a><span class="sync">{model ? `Updated ${new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit', timeZone: zone }).format(new Date(model.loadedAt))}` : 'Connection unavailable'}</span></nav>
    {#if !model}
        <main class="unavailable"><h1>Work is unavailable right now</h1><p>{data.unavailable}</p><button onclick={refresh}>Try again</button></main>
    {:else}
        {#if !model.workInventoryComplete || refreshFailed || model.observationError || model.snapshot?.observer_error || stale}
            <div class="connection" role="status">{!model.workInventoryComplete ? 'The active-work limit was reached. Counts show the loaded subset; check Prefect for the full inventory.' : refreshFailed ? 'Refresh failed. You’re reading the last loaded information.' : model.observationError ? 'Machine history is unavailable. Requests and results are still readable.' : !model.snapshot ? 'Machine history has not been published yet.' : 'Machine observations are delayed. Requests and results come directly from Prefect.'}</div>
        {/if}
        {#if system}
            <main><SystemMap {model} {now} {formatDate} /></main>
        {:else}
            <main class="workspace" class:reading={showReader}>
                <section class="inbox" aria-label="Work inbox">
                    <div class="inbox-heading"><h1>Your work</h1><p>Read what was requested, what came back, and what needs a closer look.</p></div>
                    <div class="inbox-controls"><label for="work-search" class="sr-only">Search requests</label><input id="work-search" type="search" placeholder="Search requests or repositories" bind:value={search} />
                        <div class="filters" aria-label="Filter work">{#each filters as item}<button aria-pressed={filter === item.value} onclick={() => goto(`?view=work&filter=${item.value}`, { noScroll: true, keepFocus: true })}>{item.label}<span>{counts[item.value]}</span></button>{/each}</div>
                    </div>
                    <div class="work-list">
                        {#each runs as run (run.id)}
                            {@const runAttempt = attempts.find(item => item.vm === run.infrastructure_pid)}
                            <button class="work-item" class:selected={selected?.id === run.id} aria-current={selected?.id === run.id ? 'true' : undefined} onclick={() => openRun(run.id)}>
                                <div class="item-meta"><span class:problem={needsAttention(run, runAttempt)} class:working={isWorking(run)}>{workStatus(run)}</span><time datetime={run.created}>{formatShortDate(run.created)}</time></div>
                                <h2>{workTitle(run)}</h2><p>{repository(run) ?? model.deployments.find(item => item.id === run.deployment_id)?.name ?? 'Workflow'}</p>
                                {#if runAttempt && ['retained', 'missing'].includes(runAttempt.phase)}<span class="cleanup-warning">Machine needs attention</span>{/if}
                            </button>
                        {:else}<div class="list-empty"><h2>{model.runs.length ? 'No matching work' : 'No requests yet'}</h2><p>{model.runs.length ? 'Try another search or filter.' : 'Work delegated through the configured workflows will appear here.'}</p>{#if model.runs.length}<button onclick={() => { search = ''; filter = 'all'; }}>Clear filters</button>{/if}</div>{/each}
                    </div>
                    <footer class="inbox-footer">Recent requests in the exe.dev pool. <a href="/gardener">See the execution path</a></footer>
                </section>
                <section class="reading-pane" aria-label="Work details">
                    <a class="back" href="?view=work">‹ All work</a>
                    {#if selected}
                        {#key selected.id}<WorkReader run={selected} result={model.result} resultError={model.resultError} {attempt} {stale} {formatDate} />{/key}
                    {:else}<div class="reader-empty"><h2>Every request has a place to come back to.</h2><p>Choose a request to read its answer and inspect its execution.</p><a href="/gardener">Explore how Gardener works</a></div>{/if}
                </section>
            </main>
        {/if}
    {/if}
</div>

<style>
    .gardener { --canvas: #eef3f7; --surface: #ffffff; --ink: #142536; --muted: #526477; --line: #ccd7e2; --wash: #f2f6fa; --blue: #205bb1; --selection: #e6effc; --green: #146749; --green-wash: #e5f3ec; --red: #a52d32; --red-wash: #fbecee; color: var(--ink); background: var(--canvas); min-height: 100vh; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 17px; }
    .dark { --canvas: #172331; --surface: #213141; --ink: #f2f6fa; --muted: #c0cfde; --line: #4a6175; --wash: #293c4e; --blue: #a9caff; --selection: #30496a; --green: #9de2be; --green-wash: #254738; --red: #ffb6ba; --red-wash: #4b3038; }
    .gardener :global(*:focus-visible) { outline: 3px solid var(--blue); outline-offset: 3px; }
    .app-header { display: flex; justify-content: space-between; align-items: center; gap: 24px; padding: 22px 32px; background: var(--surface); }
    .identity { display: flex; align-items: baseline; gap: 20px; } .brand { font-size: 27px; font-weight: 700; letter-spacing: -.045em; } .identity > span { color: var(--muted); font-size: 16px; }
    .utilities { display: flex; align-items: center; gap: 18px; font-size: 15px; } .utilities a, .utilities button { min-height: 44px; display: inline-flex; align-items: center; } .utilities a { color: var(--muted); }
    .refresh { border: 1px solid var(--line); padding: 8px 14px; border-radius: 8px; } button:disabled { opacity: .6; }
    .views { display: flex; gap: 30px; align-items: stretch; background: var(--surface); padding: 0 32px; border-bottom: 1px solid var(--line); }
    .views > a { padding: 16px 2px; color: var(--muted); border-bottom: 3px solid transparent; font-weight: 600; }
    .views > a[aria-current] { color: var(--blue); border-bottom-color: var(--blue); }
    .sync { margin-left: auto; align-self: center; font-size: 14px; color: var(--muted); }
    .workspace { display: grid; grid-template-columns: minmax(320px, 370px) minmax(0, 1fr); max-width: 1440px; margin: 0 auto; min-height: calc(100vh - 152px); }
    .inbox { border-right: 1px solid var(--line); } .inbox-heading { padding: 28px 24px 20px; } h1 { font-size: 25px; font-weight: 650; letter-spacing: -.025em; } .inbox-heading p { color: var(--muted); font-size: 16px; line-height: 1.6; margin-top: 10px; }
    .inbox-controls { padding: 0 20px 20px; } input { width: 100%; font-size: 16px; padding: 12px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; min-height: 46px; color: var(--ink); } input::placeholder { color: var(--muted); }
    .filters { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; } .filters button { min-height: 40px; font-size: 14px; border: 1px solid var(--line); border-radius: 20px; padding: 8px 12px; display: flex; gap: 8px; align-items: center; background: var(--surface); }
    .filters button span { color: var(--muted); } .filters button[aria-pressed='true'] { background: var(--ink); color: var(--surface); border-color: var(--ink); } .filters button[aria-pressed='true'] span { color: inherit; }
    .work-item { display: block; text-align: left; width: 100%; border-top: 1px solid var(--line); padding: 22px 24px; border-left: 4px solid transparent; }
    .work-item:hover { background: var(--wash); } .work-item.selected { background: var(--surface); border-left-color: var(--blue); }
    .item-meta { display: flex; justify-content: space-between; gap: 12px; color: var(--green); font-size: 14px; font-weight: 600; } .item-meta time { color: var(--muted); font-weight: 400; } .item-meta .problem { color: var(--red); } .item-meta .working { color: var(--blue); }
    .work-item h2 { font-size: 18px; line-height: 1.45; font-weight: 600; margin: 10px 0; overflow-wrap: anywhere; } .work-item p { color: var(--muted); font-size: 14px; } .cleanup-warning { display: block; color: var(--red); font-size: 14px; margin-top: 12px; }
    .reading-pane { background: var(--surface); min-width: 0; } .back { display: none; }
    .inbox-footer { font-size: 14px; color: var(--muted); padding: 24px; border-top: 1px solid var(--line); line-height: 1.6; } .inbox-footer a { display: block; color: var(--blue); margin-top: 8px; text-decoration: underline; text-underline-offset: 3px; }
    .connection { padding: 14px 32px; border-bottom: 1px solid var(--line); background: var(--selection); color: var(--ink); line-height: 1.5; font-size: 16px; }
    .list-empty, .reader-empty, .unavailable { padding: 32px 24px; } .list-empty h2, .reader-empty h2 { font-size: 22px; font-weight: 600; } .list-empty p, .reader-empty p, .unavailable p { margin: 12px 0 20px; line-height: 1.6; color: var(--muted); } .list-empty button, .reader-empty a, .unavailable button { color: var(--blue); text-decoration: underline; padding: 10px 0; }
    @media (min-width: 1440px) { .workspace { border-left: 1px solid var(--line); border-right: 1px solid var(--line); } }
    @media (max-width: 760px) { .app-header { padding: 16px 20px; gap: 12px; } .identity { display: block; } .brand { font-size: 25px; } .identity > span { display: block; font-size: 14px; margin-top: 2px; } .utilities { gap: 14px; } .utilities > a { display: none; } .utilities .refresh { padding: 6px 10px; } .views { padding: 0 20px; gap: 24px; } .sync { font-size: 12px; } .workspace { display: block; } .inbox { border-right: 0; } .inbox-heading { padding: 24px 20px 16px; } .work-item { padding: 22px 20px; } .reading-pane { display: none; } .reading .inbox { display: none; } .reading .reading-pane { display: block; } .back { display: block; padding: 20px 20px 0; color: var(--blue); font-weight: 600; } .connection { padding: 14px 20px; } .filters button { min-height: 44px; } }
</style>
