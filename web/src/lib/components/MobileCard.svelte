<script lang="ts">
	import type { Card } from '$lib/types';
	import SourceBadge from '$lib/components/SourceBadge.svelte';
	import { hashColor, timeAgo, parseInlineCode, authorUrl, originUrl } from '$lib/format';

	let { card }: { card: Card } = $props();

	let segments = $derived(parseInlineCode(card.title));
	let author = $derived(authorUrl(card));
	let origin = $derived(originUrl(card));
</script>

<div class="bg-gray-900 rounded-lg p-4 space-y-2">
	<div class="flex items-center justify-between gap-2">
		<div class="flex items-center gap-2 min-w-0">
			<SourceBadge kind={card.source} />
			{#if card.meta.repo}
				<a href={origin} target="_blank" rel="noopener noreferrer" class="truncate">
					<span class="inline-block px-2 py-0.5 rounded-full text-xs border {hashColor(String(card.meta.repo))}">
						{card.meta.repo}
					</span>
				</a>
			{/if}
		</div>
		<div class="text-right shrink-0">
			<div class="text-gray-300 font-mono text-xs">{card.score.toFixed(1)}</div>
			<div class="text-gray-400 text-xs">{timeAgo(card.updated)}</div>
		</div>
	</div>

	<a
		href={card.url}
		target="_blank"
		rel="noopener noreferrer"
		class="block text-gray-200 hover:text-white hover:underline text-sm leading-snug"
	>
		<span class="text-gray-400">#{card.meta.number}</span>
		{#each segments as seg, i (i)}
			{#if seg.code}
				<code class="bg-gray-700/60 px-1 rounded text-xs">{seg.code}</code>
			{:else}
				{seg.text}
			{/if}
		{/each}
	</a>

	<div class="flex items-center justify-between gap-2 text-xs">
		<div>
			{#if card.meta.user}
				{#if author}
					<a href={author} class="text-gray-400 hover:text-gray-200" target="_blank" rel="noopener noreferrer">
						@{card.meta.user}
					</a>
				{:else}
					<span class="text-gray-400">@{card.meta.user}</span>
				{/if}
			{/if}
		</div>
		<div class="flex flex-wrap justify-end gap-1">
			{#each card.tags as tag (tag)}
				<span class="inline-block px-2 py-0.5 rounded-full text-xs border {hashColor(tag)}">
					{tag}
				</span>
			{/each}
		</div>
	</div>
</div>
