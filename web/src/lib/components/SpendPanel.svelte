<script lang="ts">
	import type { SpendSummary } from '$lib/types';

	let { spend }: { spend: SpendSummary } = $props();

	const currency = new Intl.NumberFormat('en-US', {
		style: 'currency',
		currency: 'USD',
		minimumFractionDigits: 2,
		maximumFractionDigits: 4
	});
	const integer = new Intl.NumberFormat('en-US');

	function money(value: number): string {
		return currency.format(value ?? 0);
	}
</script>

<section class="space-y-4" aria-label="LLM spend">
	<div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
		<div class="bg-gray-800 rounded-lg px-5 py-4">
			<p class="text-3xl font-semibold text-gray-100 tabular-nums">{money(spend.total_24h)}</p>
			<p class="text-xs text-gray-400 mt-1 uppercase tracking-wider">24h llm spend</p>
		</div>
		<div class="bg-gray-800 rounded-lg px-5 py-4">
			<p class="text-3xl font-semibold text-gray-100 tabular-nums">{money(spend.total_7d)}</p>
			<p class="text-xs text-gray-400 mt-1 uppercase tracking-wider">7d llm spend</p>
		</div>
		<div class="bg-gray-800 rounded-lg px-5 py-4">
			<p class="text-3xl font-semibold text-gray-100 tabular-nums">{money(spend.total_all)}</p>
			<p class="text-xs text-gray-400 mt-1 uppercase tracking-wider">tracked total</p>
		</div>
		<div class="bg-gray-800 rounded-lg px-5 py-4">
			<p class="text-3xl font-semibold text-gray-100 tabular-nums">{integer.format(spend.requests_24h)}</p>
			<p class="text-xs text-gray-400 mt-1 uppercase tracking-wider">24h requests</p>
		</div>
	</div>

	<div class="grid lg:grid-cols-2 gap-4">
		<div class="bg-gray-900 rounded-lg overflow-hidden">
			<div class="px-4 py-3 border-b border-gray-800 text-sm text-gray-300">spend by flow, 7d</div>
			<table class="w-full text-sm">
				<thead class="text-gray-500">
					<tr class="border-b border-gray-800">
						<th class="text-left px-4 py-2 font-normal">flow</th>
						<th class="text-right px-4 py-2 font-normal">cost</th>
						<th class="text-right px-4 py-2 font-normal">tokens</th>
					</tr>
				</thead>
				<tbody>
					{#each spend.by_flow_7d as row (row.flow_name)}
						<tr class="border-b border-gray-800/70 last:border-0">
							<td class="px-4 py-2 text-gray-200">{row.flow_name}</td>
							<td class="px-4 py-2 text-right text-gray-100 tabular-nums">{money(row.cost_usd)}</td>
							<td class="px-4 py-2 text-right text-gray-400 tabular-nums">
								{integer.format(row.input_tokens + row.output_tokens)}
							</td>
						</tr>
					{:else}
						<tr>
							<td class="px-4 py-4 text-gray-500" colspan="3">no tracked spend yet</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		<div class="bg-gray-900 rounded-lg overflow-hidden">
			<div class="px-4 py-3 border-b border-gray-800 text-sm text-gray-300">recent calls</div>
			<table class="w-full text-sm">
				<thead class="text-gray-500">
					<tr class="border-b border-gray-800">
						<th class="text-left px-4 py-2 font-normal">task</th>
						<th class="text-left px-4 py-2 font-normal">model</th>
						<th class="text-right px-4 py-2 font-normal">cost</th>
					</tr>
				</thead>
				<tbody>
					{#each spend.recent as row (`${row.recorded_at}-${row.flow_name}-${row.task_name}`)}
						<tr class="border-b border-gray-800/70 last:border-0">
							<td class="px-4 py-2">
								<p class="text-gray-200">{row.flow_name}</p>
								<p class="text-xs text-gray-500">{row.task_name}</p>
							</td>
							<td class="px-4 py-2 text-gray-400">{row.model}</td>
							<td class="px-4 py-2 text-right text-gray-100 tabular-nums">{money(row.cost_usd)}</td>
						</tr>
					{:else}
						<tr>
							<td class="px-4 py-4 text-gray-500" colspan="3">no tracked calls yet</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</div>
</section>
