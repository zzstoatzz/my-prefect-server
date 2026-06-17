<script lang="ts">
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import type { CostRollup, InfraCostSnapshot, SpendByFlow, SpendSummary } from '$lib/types';

	let { spend, costs }: { spend: SpendSummary; costs: InfraCostSnapshot | null } = $props();

	// ── formatting ──────────────────────────────────────────────────────────
	const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
	const compact = new Intl.NumberFormat('en-US', { notation: 'compact' });
	const cents = (c: number) => usd.format((c ?? 0) / 100);
	function dollars(v: number): string {
		const a = Math.abs(v ?? 0);
		if (a === 0) return '$0';
		if (a >= 0.01) return usd.format(v);
		const d = Math.min(8, Math.max(4, Math.ceil(-Math.log10(a)) + 1));
		return `$${v.toFixed(d).replace(/0+$/, '').replace(/\.$/, '')}`;
	}

	// ── palette + repo links ────────────────────────────────────────────────
	const PALETTE = ['#22d3ee', '#a78bfa', '#34d399', '#fbbf24', '#f472b6', '#60a5fa', '#fb923c', '#4ade80', '#e879f9', '#2dd4bf', '#f87171', '#94a3b8'];
	const REPO: Record<string, string> = {
		relays: 'relay', 'plyr.fm': 'plyr.fm', typeahead: 'typeahead', prefect: 'my-prefect-server',
		'standard.site': 'leaflet-search', trending: 'coral', bufo: 'find-bufo', labelz: 'labelz',
		phi: 'bot'
	};
	const repoUrl = (key: string) => (REPO[key] ? `https://tangled.org/zzstoatzz.io/${REPO[key]}` : null);

	// LLM flows are Prefect flows defined in this repo's flows/ dir; link each to
	// its source. Most map name→name.py; a few have differently-named files.
	const FLOW_FILE: Record<string, string> = {
		'rebuild-atlas': 'atlas.py', 'phi-atlas': 'phi_atlas.py', 'pds-records': 'pds_records.py'
	};
	const flowUrl = (name: string) =>
		`https://tangled.org/zzstoatzz.io/my-prefect-server/blob/main/flows/${FLOW_FILE[name] ?? name.replace(/-/g, '_') + '.py'}`;

	// ── persisted ui state (collapsed by default) ───────────────────────────
	type View = 'project' | 'provider';
	type Win = '24h' | '7d' | '30d' | 'all';
	let open = $state(false);
	let view = $state<View>('project');
	let win = $state<Win>('7d');

	onMount(() => {
		if (localStorage.getItem('hub:costs:open') === '1') open = true;
		const v = localStorage.getItem('hub:costs:view');
		if (v === 'project' || v === 'provider') view = v;
		const w = localStorage.getItem('hub:costs:win') as Win | null;
		if (w && ['24h', '7d', '30d', 'all'].includes(w)) win = w;
	});
	$effect(() => { if (browser) localStorage.setItem('hub:costs:open', open ? '1' : '0'); });
	$effect(() => { if (browser) localStorage.setItem('hub:costs:view', view); });
	$effect(() => { if (browser) localStorage.setItem('hub:costs:win', win); });

	// ── infra ────────────────────────────────────────────────────────────────
	const rows = $derived<CostRollup[]>(costs ? (view === 'project' ? costs.byProject : costs.byProvider) : []);
	const infraMax = $derived(Math.max(1, ...rows.map((r) => r.amount)));
	const anyEstimated = $derived((costs?.lineItems ?? []).some((i) => i.estimated));
	const asOf = $derived(costs ? new Date(costs.generatedAt).toLocaleDateString() : '');

	// ── llm ────────────────────────────────────────────────────────────────
	const llmTotal = $derived(win === '24h' ? spend.total_24h : win === '7d' ? spend.total_7d : win === '30d' ? spend.total_30d : spend.total_all);
	const llmCalls = $derived(win === '24h' ? spend.requests_24h : win === '7d' ? spend.requests_7d : win === '30d' ? spend.requests_30d : spend.requests_all);
	const flows = $derived<SpendByFlow[]>(
		(win === '24h' ? spend.by_flow_24h : win === '7d' ? spend.by_flow_7d : win === '30d' ? spend.by_flow_30d : spend.by_flow_all) ?? []
	);
	const topFlows = $derived(flows.slice(0, 5));
	const flowMax = $derived(Math.max(0, ...topFlows.map((f) => f.cost_usd)));

	const windows: Win[] = ['24h', '7d', '30d', 'all'];
</script>

<section class="overflow-hidden rounded-lg border border-gray-800 bg-gray-900 shadow-[0_18px_60px_rgba(0,0,0,0.22)]">
	<!-- header / collapse toggle -->
	<button
		type="button"
		class="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left hover:bg-gray-850/40 sm:px-5"
		aria-expanded={open}
		onclick={() => (open = !open)}
	>
		<span class="inline-block text-gray-500 transition-transform" class:rotate-90={open}>▶</span>
		<span class="text-sm font-medium text-gray-200">costs</span>
		<span class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
			<span class="text-gray-400">infra <span class="font-medium text-gray-200 tabular-nums">{cents(costs?.total ?? 0)}</span>/mo</span>
			<span aria-hidden="true">·</span>
			<span class="text-gray-400">llm <span class="font-medium text-gray-200 tabular-nums">{dollars(llmTotal)}</span> ({win})</span>
		</span>
		{#if costs}<span class="ml-auto rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2 py-0.5 text-[11px] text-emerald-300">as of {asOf}</span>{/if}
	</button>

	{#if open}
		<div class="grid grid-cols-1 border-t border-gray-800 lg:grid-cols-2">
			<!-- infra -->
			<div class="border-b border-gray-800 px-4 py-4 sm:px-5 lg:border-b-0 lg:border-r">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<div>
						<p class="text-xs font-medium uppercase tracking-wider text-gray-400">infrastructure · monthly</p>
						<p class="mt-1 text-2xl font-semibold leading-none text-gray-50 tabular-nums">{cents(costs?.total ?? 0)}</p>
						<p class="mt-1 text-[11px] text-cyan-300/90">estimated{anyEstimated ? ' · ~ partly estimated' : ''}</p>
					</div>
					<div class="flex rounded-md border border-gray-800 bg-gray-950/70 p-0.5">
						{#each [{ k: 'project', l: 'project' }, { k: 'provider', l: 'provider' }] as o (o.k)}
							<button type="button" aria-pressed={view === o.k}
								class={['rounded px-2 py-1 text-xs transition', view === o.k ? 'bg-cyan-400 text-gray-950' : 'text-gray-400 hover:text-gray-100']}
								onclick={() => (view = o.k as View)}>{o.l}</button>
						{/each}
					</div>
				</div>

				<div class="mt-3 space-y-2">
					{#each rows as r, i (r.key)}
						{@const url = view === 'project' ? repoUrl(r.key) : null}
						<div>
							<div class="flex items-baseline justify-between gap-3 text-sm">
								<span class="truncate">
									{#if url}
										<a href={url} target="_blank" rel="noopener" class="text-gray-200 underline-offset-2 hover:text-white hover:underline">{r.key}</a>
									{:else}
										<span class="text-gray-200">{r.key}</span>
									{/if}{#if r.estimated}<span class="ml-1 text-xs text-amber-300/80" title="includes estimated figures">~</span>{/if}
								</span>
								<span class="shrink-0 font-medium tabular-nums text-gray-50">{cents(r.amount)}</span>
							</div>
							<div class="mt-1 h-1.5 overflow-hidden rounded-full bg-gray-800">
								<div class="h-full rounded-full" style={`width:${Math.max(3, (r.amount / infraMax) * 100)}%;background:${PALETTE[i % PALETTE.length]}`}></div>
							</div>
						</div>
					{:else}
						<p class="rounded-md border border-dashed border-gray-800 px-3 py-6 text-center text-sm text-gray-500">no cost snapshot yet</p>
					{/each}
				</div>
			</div>

			<!-- llm -->
			<div class="px-4 py-4 sm:px-5">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<div>
						<p class="text-xs font-medium uppercase tracking-wider text-gray-400">llm api · {win}</p>
						<p class="mt-1 text-2xl font-semibold leading-none text-gray-50 tabular-nums">{dollars(llmTotal)}</p>
						<p class="mt-1 text-[11px] text-gray-500">{compact.format(llmCalls)} calls · live</p>
					</div>
					<div class="flex rounded-md border border-gray-800 bg-gray-950/70 p-0.5">
						{#each windows as w (w)}
							<button type="button" aria-pressed={win === w}
								class={['rounded px-2 py-1 text-xs transition', win === w ? 'bg-cyan-400 text-gray-950' : 'text-gray-400 hover:text-gray-100']}
								onclick={() => (win = w)}>{w}</button>
						{/each}
					</div>
				</div>

				<p class="mt-3 mb-2 text-[11px] uppercase tracking-wider text-gray-500">top flows</p>
				<div class="space-y-2">
					{#each topFlows as f, i (f.flow_name)}
						<div>
							<div class="flex items-baseline justify-between gap-3 text-sm">
								<span class="truncate">
									<span class="mr-1.5 text-xs text-gray-600">{i + 1}</span><a
										href={flowUrl(f.flow_name)}
										target="_blank"
										rel="noopener"
										class="text-gray-200 underline-offset-2 hover:text-white hover:underline">{f.flow_name}</a>
								</span>
								<span class="shrink-0 font-medium tabular-nums text-gray-50">{dollars(f.cost_usd)}</span>
							</div>
							<div class="mt-1 h-1.5 overflow-hidden rounded-full bg-gray-800">
								<div class="h-full rounded-full bg-cyan-300" style={`width:${flowMax > 0 ? Math.max(3, (f.cost_usd / flowMax) * 100) : 0}%`}></div>
							</div>
						</div>
					{:else}
						<p class="rounded-md border border-dashed border-gray-800 px-3 py-6 text-center text-sm text-gray-500">no tracked spend in this window</p>
					{/each}
				</div>
			</div>
		</div>
	{/if}
</section>
