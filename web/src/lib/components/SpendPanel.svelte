<script lang="ts">
	import type { SpendByFlow, SpendSummary } from '$lib/types';

	let { spend }: { spend: SpendSummary } = $props();

	type SpendWindow = '24h' | '7d' | '30d' | 'all';

	const currency = new Intl.NumberFormat('en-US', {
		style: 'currency',
		currency: 'USD',
		minimumFractionDigits: 2,
		maximumFractionDigits: 4
	});
	const compactInteger = new Intl.NumberFormat('en-US', { notation: 'compact' });

	let selectedWindow = $state<SpendWindow>('7d');

	const windows: Array<{ key: SpendWindow; label: string }> = [
		{ key: '24h', label: '24h' },
		{ key: '7d', label: '7d' },
		{ key: '30d', label: '30d' },
		{ key: 'all', label: 'all' }
	];

	function money(value: number): string {
		const amount = value ?? 0;
		const abs = Math.abs(amount);
		if (abs === 0) return '$0';
		if (abs >= 0.01) return currency.format(amount);

		const digits = Math.min(8, Math.max(4, Math.ceil(-Math.log10(abs)) + 1));
		return `$${amount.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '')}`;
	}

	function windowTotal(window: SpendWindow): number {
		if (window === '24h') return spend.total_24h;
		if (window === '7d') return spend.total_7d;
		if (window === '30d') return spend.total_30d;
		return spend.total_all;
	}

	function windowRequests(window: SpendWindow): number {
		if (window === '24h') return spend.requests_24h;
		if (window === '7d') return spend.requests_7d;
		if (window === '30d') return spend.requests_30d;
		return spend.requests_all;
	}

	function windowFlows(window: SpendWindow): SpendByFlow[] {
		if (window === '24h') return spend.by_flow_24h;
		if (window === '7d') return spend.by_flow_7d;
		if (window === '30d') return spend.by_flow_30d;
		return spend.by_flow_all;
	}

	const selectedTotal = $derived(windowTotal(selectedWindow));
	const selectedRequests = $derived(windowRequests(selectedWindow));
	const topFlows = $derived(windowFlows(selectedWindow).slice(0, 4));
	const maxFlowCost = $derived(Math.max(0, ...topFlows.map((row) => row.cost_usd)));
	const recentCalls = $derived(spend.recent.slice(0, 4));
</script>

<section
	class="overflow-hidden rounded-lg border border-gray-800 bg-gray-900 shadow-[0_18px_60px_rgba(0,0,0,0.22)]"
	aria-label="LLM spend"
>
	<div class="grid gap-0 lg:grid-cols-[0.9fr_1.1fr_1.25fr]">
		<div class="border-b border-gray-800 px-5 py-4 lg:border-b-0 lg:border-r">
			<div class="flex items-start justify-between gap-4">
				<div>
					<p class="text-sm font-medium text-gray-200">llm spend</p>
					<p class="mt-1 text-xs text-gray-500">live from llm-spend.jsonl</p>
				</div>
				<span class="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2 py-0.5 text-xs text-emerald-300">
					live
				</span>
			</div>

			<div class="mt-4 grid grid-cols-[1fr_auto] items-end gap-4">
				<div>
					<p class="text-4xl font-semibold leading-none text-gray-50 tabular-nums">{money(selectedTotal)}</p>
					<p class="mt-2 text-[11px] uppercase tracking-wider text-cyan-300">{selectedWindow} selected</p>
				</div>
				<div class="text-right">
					<p class="text-2xl font-semibold text-gray-100 tabular-nums">
						{compactInteger.format(selectedRequests)}
					</p>
					<p class="mt-2 text-[11px] uppercase tracking-wider text-gray-500">calls</p>
				</div>
			</div>

			<div class="mt-5 grid grid-cols-2 gap-2 text-sm">
				<div class="rounded-md border border-gray-800 bg-gray-950/45 px-3 py-2">
					<p class="text-gray-500">24h</p>
					<p class="mt-1 font-medium text-gray-100 tabular-nums">{money(spend.total_24h)}</p>
				</div>
				<div class="rounded-md border border-gray-800 bg-gray-950/45 px-3 py-2">
					<p class="text-gray-500">all tracked</p>
					<p class="mt-1 font-medium text-gray-100 tabular-nums">{money(spend.total_all)}</p>
				</div>
			</div>
		</div>

		<div class="border-b border-gray-800 px-5 py-4 lg:border-b-0 lg:border-r">
			<div class="flex flex-wrap items-center justify-between gap-3">
				<p class="text-sm font-medium text-gray-200">top flows</p>
				<div class="flex rounded-md border border-gray-800 bg-gray-950/70 p-0.5">
					{#each windows as window (window.key)}
						<button
							type="button"
							class={[
								'rounded px-2.5 py-1 text-xs transition',
								selectedWindow === window.key
									? 'bg-cyan-400 text-gray-950'
									: 'text-gray-400 hover:bg-gray-800 hover:text-gray-100'
							]}
							aria-pressed={selectedWindow === window.key}
							onclick={() => (selectedWindow = window.key)}
						>
							{window.label}
						</button>
					{/each}
				</div>
			</div>

			<div class="mt-4 space-y-3">
				{#each topFlows as row, index (row.flow_name)}
					<div>
						<div class="flex items-baseline justify-between gap-3">
							<div class="min-w-0">
								<p class="truncate text-sm font-medium text-gray-100">
									<span class="mr-2 text-xs text-gray-500">{index + 1}</span>{row.flow_name}
								</p>
								<p class="mt-0.5 text-xs text-gray-500 tabular-nums">
									{compactInteger.format(row.input_tokens + row.output_tokens)} tokens ·
									{compactInteger.format(row.requests)} calls
								</p>
							</div>
							<p class="text-sm font-semibold text-gray-50 tabular-nums">{money(row.cost_usd)}</p>
						</div>
						<div class="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-800">
							<div
								class="h-full rounded-full bg-cyan-300"
								style={`width: ${maxFlowCost > 0 ? Math.max(6, (row.cost_usd / maxFlowCost) * 100) : 0}%`}
							></div>
						</div>
					</div>
				{:else}
					<p class="rounded-md border border-dashed border-gray-800 px-3 py-6 text-center text-sm text-gray-500">
						no tracked spend in this window
					</p>
				{/each}
			</div>
		</div>

		<div class="px-5 py-4">
			<div class="flex items-center justify-between gap-4">
				<p class="text-sm font-medium text-gray-200">recent calls</p>
				<p class="text-xs text-gray-500">{recentCalls.length} latest</p>
			</div>
			<div class="mt-4 divide-y divide-gray-800">
				{#each recentCalls as row (`${row.recorded_at}-${row.flow_name}-${row.task_name}`)}
					<div class="grid grid-cols-[minmax(0,1fr)_auto] gap-3 py-2 first:pt-0 last:pb-0">
						<div class="min-w-0">
							<p class="truncate text-sm font-medium text-gray-100">{row.flow_name}</p>
							<p class="truncate text-xs text-gray-500">{row.task_name}</p>
							<p class="mt-0.5 truncate text-[11px] text-amber-300/80">{row.model}</p>
						</div>
						<p class="rounded bg-gray-950/70 px-2 py-1 text-sm font-medium text-gray-50 tabular-nums">
							{money(row.cost_usd)}
						</p>
					</div>
				{:else}
					<p class="rounded-md border border-dashed border-gray-800 px-3 py-6 text-center text-sm text-gray-500">
						no tracked calls yet
					</p>
				{/each}
			</div>
		</div>
	</div>
</section>
