<script lang="ts">
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import type { CostRollup, InfraCostSnapshot } from '$lib/types';

	let { costs }: { costs: InfraCostSnapshot | null } = $props();

	const currency = new Intl.NumberFormat('en-US', {
		style: 'currency',
		currency: 'USD',
		minimumFractionDigits: 2,
		maximumFractionDigits: 2
	});
	const money = (cents: number) => currency.format((cents ?? 0) / 100);

	// colorful palette tuned for the dark theme; cycles if there are more slices
	const PALETTE = [
		'#22d3ee', '#a78bfa', '#34d399', '#fbbf24', '#f472b6', '#60a5fa',
		'#fb923c', '#4ade80', '#e879f9', '#2dd4bf', '#f87171', '#94a3b8'
	];
	const colorAt = (i: number) => PALETTE[i % PALETTE.length];

	type View = 'project' | 'provider';

	// collapsed by default; both the open/closed state and the chosen view are
	// persisted in localStorage so the panel stays how you left it.
	const OPEN_KEY = 'hub:costPanel:open';
	const VIEW_KEY = 'hub:costPanel:view';
	let open = $state(false);
	let view = $state<View>('project');

	onMount(() => {
		if (localStorage.getItem(OPEN_KEY) === '1') open = true;
		const v = localStorage.getItem(VIEW_KEY);
		if (v === 'project' || v === 'provider') view = v;
	});
	$effect(() => {
		if (browser) localStorage.setItem(OPEN_KEY, open ? '1' : '0');
	});
	$effect(() => {
		if (browser) localStorage.setItem(VIEW_KEY, view);
	});

	const rows = $derived<CostRollup[]>(
		costs ? (view === 'project' ? costs.byProject : costs.byProvider) : []
	);
	const total = $derived(rows.reduce((s, r) => s + r.amount, 0));
	const anyEstimated = $derived((costs?.lineItems ?? []).some((i) => i.estimated));
	const asOf = $derived(costs ? new Date(costs.generatedAt).toLocaleDateString() : '');

	// donut segments: circumference normalized to 100 so dasharray = percent.
	const segments = $derived.by(() => {
		let offset = 25; // start at 12 o'clock
		return rows.map((r, i) => {
			const pct = total > 0 ? (r.amount / total) * 100 : 0;
			const seg = { key: r.key, color: colorAt(i), pct, offset, estimated: r.estimated, amount: r.amount };
			offset = (offset - pct + 100) % 100;
			return seg;
		});
	});
</script>

<section
	class="overflow-hidden rounded-lg border border-gray-800 bg-gray-900 shadow-[0_18px_60px_rgba(0,0,0,0.22)]"
	aria-label="infrastructure cost"
>
	<div class="grid gap-0 lg:grid-cols-[0.9fr_2fr]">
		<div class="border-b border-gray-800 px-5 py-4 lg:border-b-0 lg:border-r">
			<div class="flex items-start justify-between gap-4">
				<div>
					<p class="text-sm font-medium text-gray-200">infra spend</p>
					<p class="mt-1 text-xs text-gray-500">monthly · from PDS cost snapshot</p>
				</div>
				<span class="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2 py-0.5 text-xs text-emerald-300">
					{costs ? `as of ${asOf}` : 'no data'}
				</span>
			</div>
			<div class="mt-4">
				<p class="text-4xl font-semibold leading-none text-gray-50 tabular-nums">{money(costs?.total ?? 0)}</p>
				<p class="mt-2 text-[11px] uppercase tracking-wider text-cyan-300">
					estimated monthly{anyEstimated ? ' · ~ partly estimated' : ''}
				</p>
			</div>
		</div>

		<div class="px-5 py-4">
			<div class="flex flex-wrap items-center justify-between gap-3">
				<button
					type="button"
					class="flex items-center gap-2 text-sm font-medium text-gray-200 hover:text-white"
					aria-expanded={open}
					onclick={() => (open = !open)}
				>
					<span class="inline-block text-gray-500 transition-transform" class:rotate-90={open}>▶</span>
					breakdown
					{#if !open}<span class="text-xs font-normal text-gray-500">({rows.length} {view}s — click to expand)</span>{/if}
				</button>
				{#if open}
					<div class="flex rounded-md border border-gray-800 bg-gray-950/70 p-0.5">
						{#each [{ key: 'project', label: 'by project' }, { key: 'provider', label: 'by provider' }] as opt (opt.key)}
							<button
								type="button"
								class={[
									'rounded px-2.5 py-1 text-xs transition',
									view === opt.key ? 'bg-cyan-400 text-gray-950' : 'text-gray-400 hover:bg-gray-800 hover:text-gray-100'
								]}
								aria-pressed={view === opt.key}
								onclick={() => (view = opt.key as View)}
							>
								{opt.label}
							</button>
						{/each}
					</div>
				{/if}
			</div>

			{#if open}
				{#if rows.length}
					<div class="mt-4 flex flex-col items-center gap-6 sm:flex-row sm:items-start">
						<!-- donut -->
						<svg viewBox="0 0 42 42" class="h-36 w-36 shrink-0" role="img" aria-label="cost breakdown chart">
							<circle cx="21" cy="21" r="15.915" fill="none" stroke="#1f2937" stroke-width="5" />
							{#each segments as s (s.key)}
								<circle
									cx="21" cy="21" r="15.915" fill="none"
									stroke={s.color} stroke-width="5"
									stroke-dasharray="{s.pct} {100 - s.pct}"
									stroke-dashoffset={s.offset}
								/>
							{/each}
							<text x="21" y="20.5" text-anchor="middle" class="fill-gray-100" style="font-size:5px;font-weight:700">{money(total).replace('.00', '')}</text>
							<text x="21" y="25.5" text-anchor="middle" class="fill-gray-500" style="font-size:2.6px;letter-spacing:.05em">/MO</text>
						</svg>

						<!-- legend -->
						<div class="min-w-0 flex-1 space-y-1.5">
							{#each segments as s (s.key)}
								<div class="flex items-center gap-2 text-sm">
									<span class="h-2.5 w-2.5 shrink-0 rounded-sm" style={`background:${s.color}`}></span>
									<span class="truncate text-gray-200">{s.key}{#if s.estimated}<span class="ml-1 text-xs text-amber-300/80" title="includes estimated figures">~</span>{/if}</span>
									<span class="ml-auto shrink-0 tabular-nums text-gray-400">{s.pct.toFixed(0)}%</span>
									<span class="w-16 shrink-0 text-right font-medium tabular-nums text-gray-50">{money(s.amount)}</span>
								</div>
							{/each}
						</div>
					</div>
				{:else}
					<p class="mt-4 rounded-md border border-dashed border-gray-800 px-3 py-6 text-center text-sm text-gray-500">
						no cost snapshot collected yet
					</p>
				{/if}
			{/if}
		</div>
	</div>
</section>
