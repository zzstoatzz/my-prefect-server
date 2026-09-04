<script lang="ts">
	import { browser } from '$app/environment';
	import type { Card } from '$lib/types';
	import CardRow from '$lib/components/CardRow.svelte';
	import MobileCard from '$lib/components/MobileCard.svelte';

	let { cards }: { cards: Card[] } = $props();

	const PAGE = 40;

	let sortCol: string = $state('score');
	let sortDir: 'asc' | 'desc' = $state('desc');
	let visible = $state(PAGE);
	let sentinel = $state<HTMLDivElement | null>(null);

	let sorted: Card[] = $derived.by(() => {
		const copy = [...cards];
		const dir = sortDir === 'asc' ? 1 : -1;
		return copy.sort((a, b) => {
			switch (sortCol) {
				case 'source':
					return a.source.localeCompare(b.source) * dir;
				case 'item':
					return a.title.localeCompare(b.title) * dir;
				case 'author':
					return String(a.meta.user ?? '').localeCompare(String(b.meta.user ?? '')) * dir;
				case 'score':
					return (a.score - b.score) * dir;
				default:
					return 0;
			}
		});
	});

	// render only the top `visible` rows; reveal the rest on demand. the full set
	// is already in memory — this shrinks the SSR/DOM payload so the page hydrates
	// fast (and the cost panel becomes interactive immediately) instead of waiting
	// on hundreds of rendered rows.
	const shown = $derived(sorted.slice(0, visible));
	const remaining = $derived(Math.max(0, sorted.length - visible));

	// snap back to the first page when the filtered set changes (re-sorting keeps
	// the user's current depth, just reorders it).
	$effect(() => {
		void cards;
		visible = PAGE;
	});

	function showMore() {
		visible = Math.min(sorted.length, visible + PAGE);
	}

	// progressively reveal as the sentinel scrolls into view; the button below
	// is the keyboard / no-JS fallback.
	$effect(() => {
		const el = sentinel;
		if (!el || !browser) return;
		const io = new IntersectionObserver(
			(entries) => {
				if (remaining > 0 && entries.some((e) => e.isIntersecting)) showMore();
			},
			{ rootMargin: '600px' }
		);
		io.observe(el);
		return () => io.disconnect();
	});

	function toggle(col: string) {
		if (sortCol === col) {
			sortDir = sortDir === 'asc' ? 'desc' : 'asc';
		} else {
			sortCol = col;
			sortDir = 'desc';
		}
	}

	function indicator(col: string): string {
		if (sortCol !== col) return '';
		return sortDir === 'asc' ? ' ↑' : ' ↓';
	}

	function ariaSort(col: string): 'ascending' | 'descending' | 'none' {
		if (sortCol !== col) return 'none';
		return sortDir === 'asc' ? 'ascending' : 'descending';
	}
</script>

<!-- desktop table -->
<div class="bg-gray-900 rounded-lg overflow-hidden hidden md:block">
	<table class="w-full text-sm">
		<thead>
			<tr class="border-b border-gray-800">
				<th
					class="text-left px-4 py-3 font-normal cursor-pointer {sortCol === 'source' ? 'text-gray-200' : 'text-gray-400'}"
					onclick={() => toggle('source')}
					aria-sort={ariaSort('source')}
				>
					source{indicator('source')}
				</th>
				<th
					class="text-left px-4 py-3 font-normal cursor-pointer {sortCol === 'item' ? 'text-gray-200' : 'text-gray-400'}"
					onclick={() => toggle('item')}
					aria-sort={ariaSort('item')}
				>
					item{indicator('item')}
				</th>
				<th
					class="text-left px-4 py-3 font-normal cursor-pointer {sortCol === 'author' ? 'text-gray-200' : 'text-gray-400'}"
					onclick={() => toggle('author')}
					aria-sort={ariaSort('author')}
				>
					author{indicator('author')}
				</th>
				<th class="text-left px-4 py-3 text-gray-400 font-normal">tags</th>
				<th
					class="text-right px-4 py-3 font-normal cursor-pointer {sortCol === 'score' ? 'text-gray-200' : 'text-gray-400'}"
					onclick={() => toggle('score')}
					aria-sort={ariaSort('score')}
				>
					relevance{indicator('score')}
				</th>
			</tr>
		</thead>
		<tbody>
			{#each shown as card (card.id)}
				<CardRow {card} />
			{/each}
		</tbody>
	</table>
</div>

<!-- mobile cards -->
<div class="md:hidden space-y-3">
	<div class="flex items-center gap-2 mb-2">
		<select
			bind:value={sortCol}
			aria-label="sort by"
			class="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-gray-500 focus-visible:ring-2 focus-visible:ring-indigo-500/50 flex-1 min-w-0"
		>
			<option value="score">relevance</option>
			<option value="source">source</option>
			<option value="item">item</option>
			<option value="author">author</option>
		</select>
		<button
			onclick={() => sortDir = sortDir === 'asc' ? 'desc' : 'asc'}
			class="p-2 bg-gray-800 border border-gray-700 rounded text-gray-400 hover:text-gray-200 hover:bg-gray-700 focus-visible:ring-2 focus-visible:ring-indigo-500/50 transition-colors"
			aria-label="toggle sort direction: {sortDir === 'asc' ? 'ascending' : 'descending'}"
		>
			<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
				class={sortDir === 'asc' ? 'rotate-180' : ''}
			>
				<path d="M12 5v14" />
				<path d="M19 12l-7 7-7-7" />
			</svg>
		</button>
	</div>
	{#each shown as card (card.id)}
		<MobileCard {card} />
	{/each}
</div>

<!-- reveal more of the sorted list on demand. the sentinel is a stable node
	 (observer created once); scrolling it into view auto-loads, and the button
	 is the keyboard / no-JS fallback. -->
<div bind:this={sentinel} class="mt-4 flex justify-center">
	{#if remaining > 0}
		<button
			onclick={showMore}
			class="rounded-md border border-gray-800 bg-gray-900 px-4 py-2 text-sm text-gray-300 transition-colors hover:bg-gray-800 hover:text-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50"
		>
			show {Math.min(PAGE, remaining)} more · {remaining} hidden
		</button>
	{/if}
</div>
