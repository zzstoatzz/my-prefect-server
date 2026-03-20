<script lang="ts">
	import type { Card } from '$lib/types';
	import SourceBadge from '$lib/components/SourceBadge.svelte';
	import { hashColor, timeAgo, parseInlineCode, authorUrl, originUrl } from '$lib/format';

	let { card }: { card: Card } = $props();

	let segments = $derived(parseInlineCode(card.title));
	let author = $derived(authorUrl(card));
	let origin = $derived(originUrl(card));
</script>

<tr class="border-b border-gray-800/50 hover:bg-gray-800/60 transition-colors">
	<!-- source -->
	<td class="px-4 py-3">
		<SourceBadge kind={card.source} />
	</td>

	<!-- item -->
	<td class="px-4 py-3 max-w-xs">
		{#if card.meta.repo}
			<a href={origin} target="_blank" rel="noopener noreferrer">
				<span class="inline-block px-2 py-0.5 rounded-full text-xs border {hashColor(String(card.meta.repo))}">
					{card.meta.repo}
				</span>
			</a>
		{/if}
		<a
			href={card.url}
			target="_blank"
			rel="noopener noreferrer"
			class="text-gray-200 hover:text-white hover:underline ml-2"
		>
			#{card.meta.number}
			{#each segments as seg, i (i)}
				{#if seg.code}
					<code class="bg-gray-700/60 px-1 rounded text-xs">{seg.code}</code>
				{:else}
					{seg.text}
				{/if}
			{/each}
		</a>
	</td>

	<!-- author -->
	<td class="px-4 py-3">
		{#if card.meta.user}
			{#if author}
				<a href={author} class="text-gray-400 hover:text-gray-200 text-xs" target="_blank" rel="noopener noreferrer">
					@{card.meta.user}
				</a>
			{:else}
				<span class="text-gray-400 text-xs">@{card.meta.user}</span>
			{/if}
		{/if}
	</td>

	<!-- tags -->
	<td class="px-4 py-3">
		{#each card.tags as tag (tag)}
			<span class="inline-block px-2 py-0.5 rounded-full text-xs border mr-1 {hashColor(tag)}">
				{tag}
			</span>
		{/each}
	</td>

	<!-- relevance -->
	<td class="px-4 py-3 text-right">
		<div class="text-gray-300 font-mono text-xs">{card.score.toFixed(1)}</div>
		<div class="text-gray-500 text-xs">{timeAgo(card.updated)}</div>
	</td>
</tr>
