<script lang="ts">
	import type { Briefing, BriefingItem } from '$lib/server/briefing';
	import type { Card } from '$lib/types';
	import { timeAgo } from '$lib/format';

	let { briefing, cards }: { briefing: Briefing | null; cards: Card[] } = $props();

	let cardMap = $derived(
		new Map(cards.map((c) => [c.id, c]))
	);

	function urlFor(item: BriefingItem): string | null {
		return cardMap.get(item.item_id)?.url ?? null;
	}
</script>

{#if briefing}
	<div class="space-y-4">
		<h2 class="text-xl font-semibold text-gray-100">{briefing.headline}</h2>

		<div class="grid gap-4 sm:grid-cols-2">
			{#each briefing.sections as section (section.title)}
				<div class="bg-gray-800 rounded-lg px-5 py-4 space-y-3">
					<h3 class="text-sm font-medium text-gray-300 lowercase">{section.title}</h3>
					<p class="text-sm text-gray-400">{section.summary}</p>

					{#if section.items.length > 0}
						<ul class="space-y-1.5">
							{#each section.items as item (item.item_id)}
								{@const url = urlFor(item)}
								<li class="text-sm text-gray-300">
									{#if url}
										<a
											href={url}
											target="_blank"
											rel="noopener noreferrer"
											class="underline decoration-gray-600 hover:decoration-gray-400 transition-colors"
										>
											{item.note}
										</a>
									{:else}
										{item.note}
									{/if}
								</li>
							{/each}
						</ul>
					{/if}
				</div>
			{/each}
		</div>

		<p class="text-xs text-gray-500">updated {timeAgo(briefing.generated_at)}</p>
	</div>
{/if}
