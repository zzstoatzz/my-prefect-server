<script lang="ts">
	import type { Briefing, BriefingItem } from '$lib/server/briefing';
	import type { Card } from '$lib/types';
	import { timeAgo } from '$lib/format';
	import { ACCENT_STYLES, ICON_PATHS, PRIORITY_LAYOUT } from '$lib/briefing-styles';

	let { briefing, cards }: { briefing: Briefing | null; cards: Card[] } = $props();

	let cardMap = $derived(new Map(cards.map((c) => [c.id, c])));

	function urlFor(item: BriefingItem): string | null {
		return cardMap.get(item.item_id)?.url ?? null;
	}

	/** "github:prefecthq/prefect#1234" -> "prefect#1234" */
	function shortLabel(item_id: string): string {
		const after = item_id.includes(':') ? item_id.split(':')[1] : item_id;
		const match = after.match(/([^/]+)(#\d+)$/);
		return match ? `${match[1]}${match[2]}` : after;
	}
</script>

{#if briefing}
	<div class="space-y-4">
		<h2 class="text-xl font-semibold text-gray-100">{briefing.headline}</h2>

		<div class="grid gap-4 sm:grid-cols-2">
			{#each briefing.sections as section (section.title)}
				{@const accent = ACCENT_STYLES[section.accent ?? 'sky']}
				{@const layout = PRIORITY_LAYOUT[section.priority ?? 'normal']}
				{@const icon = section.icon ? ICON_PATHS[section.icon] : null}

				<div
					class="rounded-lg {accent.bgTint} {accent.border} {layout.borderWidth} {layout.padding} {layout.colSpan} space-y-3"
				>
					<div class="flex items-center gap-2">
						{#if icon}
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="h-5 w-5 shrink-0 {accent.headerText}"
							>
								<path d={icon} />
							</svg>
						{/if}
						<h3 class="{layout.titleSize} font-medium {accent.headerText} lowercase">
							{section.title}
						</h3>
					</div>

					<p class="text-sm {accent.summaryText}">{section.summary}</p>

					{#if section.items.length > 0}
						<ul class="space-y-1.5">
							{#each section.items as item (item.item_id)}
								{@const url = urlFor(item)}
								{@const highlighted = item.highlight ?? false}
								<li
									class="text-sm flex gap-2 {highlighted
										? `border-l-2 ${accent.highlightBar} pl-2 text-gray-100`
										: 'text-gray-300'}"
								>
									<span class="font-mono text-xs opacity-60 shrink-0 pt-0.5">
										{shortLabel(item.item_id)}
									</span>
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
										<span>{item.note}</span>
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
