<script lang="ts">
	import type { CostRollup, InfraCostSnapshot } from '$lib/types';

	let { costs }: { costs: InfraCostSnapshot | null } = $props();

	const currency = new Intl.NumberFormat('en-US', {
		style: 'currency',
		currency: 'USD',
		minimumFractionDigits: 2,
		maximumFractionDigits: 2
	});

	function money(cents: number): string {
		return currency.format((cents ?? 0) / 100);
	}

	type View = 'project' | 'provider';
	let view = $state<View>('project');

	const rows = $derived<CostRollup[]>(
		costs ? (view === 'project' ? costs.byProject : costs.byProvider) : []
	);
	const maxAmount = $derived(Math.max(0, ...rows.map((r) => r.amount)));
	const anyEstimated = $derived((costs?.lineItems ?? []).some((i) => i.estimated));
	const asOf = $derived(
		costs ? new Date(costs.generatedAt).toLocaleDateString() : ''
	);
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
				<span
					class="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2 py-0.5 text-xs text-emerald-300"
				>
					{costs ? `as of ${asOf}` : 'no data'}
				</span>
			</div>

			<div class="mt-4">
				<p class="text-4xl font-semibold leading-none text-gray-50 tabular-nums">
					{money(costs?.total ?? 0)}
				</p>
				<p class="mt-2 text-[11px] uppercase tracking-wider text-cyan-300">
					estimated monthly{anyEstimated ? ' · ~ partly estimated' : ''}
				</p>
			</div>
		</div>

		<div class="px-5 py-4">
			<div class="flex flex-wrap items-center justify-between gap-3">
				<p class="text-sm font-medium text-gray-200">breakdown</p>
				<div class="flex rounded-md border border-gray-800 bg-gray-950/70 p-0.5">
					{#each [{ key: 'project', label: 'by project' }, { key: 'provider', label: 'by provider' }] as opt (opt.key)}
						<button
							type="button"
							class={[
								'rounded px-2.5 py-1 text-xs transition',
								view === opt.key
									? 'bg-cyan-400 text-gray-950'
									: 'text-gray-400 hover:bg-gray-800 hover:text-gray-100'
							]}
							aria-pressed={view === opt.key}
							onclick={() => (view = opt.key as View)}
						>
							{opt.label}
						</button>
					{/each}
				</div>
			</div>

			<div class="mt-4 space-y-3">
				{#each rows as row (row.key)}
					<div>
						<div class="flex items-baseline justify-between gap-3">
							<p class="truncate text-sm font-medium text-gray-100">
								{row.key}{#if row.estimated}<span
										class="ml-1 text-xs text-amber-300/80"
										title="includes estimated figures">~</span
									>{/if}
							</p>
							<p class="text-sm font-semibold text-gray-50 tabular-nums">{money(row.amount)}</p>
						</div>
						<div class="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-800">
							<div
								class="h-full rounded-full bg-cyan-300"
								style={`width: ${maxAmount > 0 ? Math.max(6, (row.amount / maxAmount) * 100) : 0}%`}
							></div>
						</div>
					</div>
				{:else}
					<p
						class="rounded-md border border-dashed border-gray-800 px-3 py-6 text-center text-sm text-gray-500"
					>
						no cost snapshot collected yet
					</p>
				{/each}
			</div>
		</div>
	</div>
</section>
