<script lang="ts">
	import type { Briefing, BriefingItem } from '$lib/server/briefing';
	import type { Card } from '$lib/types';
	import { hashColor, timeAgo } from '$lib/format';
	import { ACCENT_STYLES, PRIORITY_LAYOUT } from '$lib/briefing-styles';

	let { briefing, cards }: { briefing: Briefing | null; cards: Card[] } = $props();

	let cardMap = $derived(new Map(cards.map((c) => [c.id, c])));

	/** cap at 4 sections for 2x2 grid */
	let sections = $derived(briefing?.sections.slice(0, 4) ?? []);

	const MY_HANDLES = new Set(['zzstoatzz', 'zzstoatzz.io']);

	function cardFor(item: BriefingItem): Card | undefined {
		return cardMap.get(item.item_id);
	}

	function urlFor(item: BriefingItem): string | null {
		return cardFor(item)?.url ?? null;
	}

	function isMine(item: BriefingItem): boolean {
		const user = cardFor(item)?.meta?.user;
		return typeof user === 'string' && MY_HANDLES.has(user);
	}

	interface ParsedItemId {
		source: string;
		repo: string;
		shortRepo: string;
		number: string;
	}

	/** "github:prefecthq/prefect#1234" -> { source, repo, shortRepo, number } */
	function parseItemId(item_id: string): ParsedItemId | null {
		const colonIdx = item_id.indexOf(':');
		if (colonIdx === -1) return null;

		const source = item_id.slice(0, colonIdx);
		const rest = item_id.slice(colonIdx + 1);

		const match = rest.match(/^(.+?)#(\d+)$/);
		if (!match) return null;

		const repo = match[1];
		const parts = repo.split('/');
		const shortRepo = parts[parts.length - 1];

		return { source, repo, shortRepo, number: match[2] };
	}

	/** build repo URL based on source */
	function repoUrl(parsed: ParsedItemId): string {
		switch (parsed.source) {
			case 'github':
				return `https://github.com/${parsed.repo}`;
			case 'tangled':
				return `https://tangled.org/zzstoatzz.io/${parsed.repo}`;
			default:
				return '#';
		}
	}
</script>

{#if briefing}
	<div class="space-y-4">
		{#if briefing.title}
			<h2 class="text-xl font-semibold text-gray-100">{briefing.title}</h2>
		{/if}
		<p class="text-sm text-gray-400">{briefing.headline}</p>

		<div class="grid gap-4 sm:grid-cols-2">
			{#each sections as section (section.title)}
				{@const accent = ACCENT_STYLES[section.accent ?? 'sky']}
				{@const layout = PRIORITY_LAYOUT[section.priority ?? 'normal']}

				<div
					class="rounded-lg {accent.bgTint} {accent.border} {layout.borderWidth} {layout.padding} space-y-3"
				>
					<div class="flex items-center gap-2">
						<span class="h-2 w-2 shrink-0 rounded-full {accent.dot}"></span>
						<h3 class="{layout.titleSize} font-medium {accent.headerText} lowercase">
							{section.title}
						</h3>
					</div>

					<p class="text-sm {accent.summaryText}">{section.summary}</p>

					{#if section.items.length > 0}
						<ul class="space-y-1.5">
							{#each section.items as item (item.item_id)}
								{@const url = urlFor(item)}
								{@const parsed = parseItemId(item.item_id)}
								{@const mine = isMine(item)}
								<li class="text-sm flex items-center gap-2 text-gray-300">
									{#if parsed}
										<a
											href={repoUrl(parsed)}
											target="_blank"
											rel="noopener noreferrer"
											class="shrink-0"
										>
											<span
												class="inline-block px-2 py-0.5 rounded-full text-xs border {hashColor(parsed.repo)}"
											>
												{parsed.shortRepo}
											</span>
										</a>
										{#if url}
											<a
												href={url}
												target="_blank"
												rel="noopener noreferrer"
												class="font-mono text-xs opacity-60 shrink-0"
											>
												#{parsed.number}
											</a>
										{:else}
											<span class="font-mono text-xs opacity-60 shrink-0">
												#{parsed.number}
											</span>
										{/if}
									{:else}
										<span class="font-mono text-xs opacity-60 shrink-0">
											{item.item_id}
										</span>
									{/if}
									{#if mine}
										<span
											class="shrink-0 text-[10px] px-1 py-px rounded bg-sky-400/15 text-sky-400 font-medium"
											>you</span
										>
									{/if}
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
