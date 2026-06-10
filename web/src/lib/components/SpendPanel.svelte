<script lang="ts">
	import type { SpendSummary } from '$lib/types';

	let { spend }: { spend: SpendSummary } = $props();

	const currency = new Intl.NumberFormat('en-US', {
		style: 'currency',
		currency: 'USD',
		minimumFractionDigits: 2,
		maximumFractionDigits: 4
	});
	const compactInteger = new Intl.NumberFormat('en-US', { notation: 'compact' });

	function money(value: number): string {
		const amount = value ?? 0;
		const abs = Math.abs(amount);
		if (abs === 0) return '$0';
		if (abs >= 0.01) return currency.format(amount);

		const digits = Math.min(8, Math.max(4, Math.ceil(-Math.log10(abs)) + 1));
		return `$${amount.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '')}`;
	}

	const topFlows = $derived(spend.by_flow_7d.slice(0, 3));
	const recentCalls = $derived(spend.recent.slice(0, 4));
</script>

<section
	class="grid gap-4 rounded-lg border border-gray-800 bg-gray-900 px-5 py-4 lg:grid-cols-[1.1fr_1fr_1.35fr]"
	aria-label="LLM spend"
>
	<div>
		<p class="text-sm text-gray-300">llm spend</p>
		<div class="mt-3 grid grid-cols-3 gap-3">
			<div>
				<p class="text-2xl font-semibold text-gray-100 tabular-nums">{money(spend.total_24h)}</p>
				<p class="mt-1 text-[11px] uppercase tracking-wider text-gray-500">24h</p>
			</div>
			<div>
				<p class="text-2xl font-semibold text-gray-100 tabular-nums">{money(spend.total_7d)}</p>
				<p class="mt-1 text-[11px] uppercase tracking-wider text-gray-500">7d</p>
			</div>
			<div>
				<p class="text-2xl font-semibold text-gray-100 tabular-nums">
					{compactInteger.format(spend.requests_24h)}
				</p>
				<p class="mt-1 text-[11px] uppercase tracking-wider text-gray-500">calls 24h</p>
			</div>
		</div>
	</div>

	<div class="border-t border-gray-800 pt-4 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
		<div class="flex items-center justify-between gap-4">
			<p class="text-sm text-gray-300">top flows, 7d</p>
			<p class="text-xs text-gray-500 tabular-nums">{money(spend.total_7d)}</p>
		</div>
		<div class="mt-3 space-y-2">
			{#each topFlows as row (row.flow_name)}
				<div class="grid grid-cols-[1fr_auto_auto] items-baseline gap-3 text-sm">
					<p class="truncate text-gray-200">{row.flow_name}</p>
					<p class="text-xs text-gray-500 tabular-nums">
						{compactInteger.format(row.input_tokens + row.output_tokens)} tokens
					</p>
					<p class="text-gray-100 tabular-nums">{money(row.cost_usd)}</p>
				</div>
			{:else}
				<p class="text-sm text-gray-500">no tracked spend yet</p>
			{/each}
		</div>
	</div>

	<div class="border-t border-gray-800 pt-4 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
		<p class="text-sm text-gray-300">recent calls</p>
		<div class="mt-3 divide-y divide-gray-800">
			{#each recentCalls as row (`${row.recorded_at}-${row.flow_name}-${row.task_name}`)}
				<div class="grid grid-cols-[minmax(0,1fr)_auto] gap-3 py-1.5 first:pt-0 last:pb-0">
					<div class="min-w-0">
						<p class="truncate text-sm text-gray-200">{row.flow_name} / {row.task_name}</p>
						<p class="truncate text-xs text-gray-500">{row.model}</p>
					</div>
					<p class="text-sm text-gray-100 tabular-nums">{money(row.cost_usd)}</p>
				</div>
			{:else}
				<p class="text-sm text-gray-500">no tracked calls yet</p>
			{/each}
		</div>
	</div>
</section>
