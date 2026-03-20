<script lang="ts">
	import type { Card } from '$lib/types';
	import CardRow from '$lib/components/CardRow.svelte';

	let { cards }: { cards: Card[] } = $props();

	let sortCol: string = $state('score');
	let sortDir: 'asc' | 'desc' = $state('desc');

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
</script>

<div class="bg-gray-900 rounded-lg overflow-hidden">
	<table class="w-full text-sm">
		<thead>
			<tr class="border-b border-gray-800">
				<th
					class="text-left px-4 py-3 font-normal cursor-pointer {sortCol === 'source' ? 'text-gray-200' : 'text-gray-400'}"
					onclick={() => toggle('source')}
				>
					source{indicator('source')}
				</th>
				<th
					class="text-left px-4 py-3 font-normal cursor-pointer {sortCol === 'item' ? 'text-gray-200' : 'text-gray-400'}"
					onclick={() => toggle('item')}
				>
					item{indicator('item')}
				</th>
				<th
					class="text-left px-4 py-3 font-normal cursor-pointer {sortCol === 'author' ? 'text-gray-200' : 'text-gray-400'}"
					onclick={() => toggle('author')}
				>
					author{indicator('author')}
				</th>
				<th class="text-left px-4 py-3 text-gray-400 font-normal">tags</th>
				<th
					class="text-right px-4 py-3 font-normal cursor-pointer {sortCol === 'score' ? 'text-gray-200' : 'text-gray-400'}"
					onclick={() => toggle('score')}
				>
					relevance{indicator('score')}
				</th>
			</tr>
		</thead>
		<tbody>
			{#each sorted as card (card.id)}
				<CardRow {card} />
			{/each}
		</tbody>
	</table>
</div>
