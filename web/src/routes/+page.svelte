<script lang="ts">
	import Briefing from '$lib/components/Briefing.svelte';
	import StatBar from '$lib/components/StatBar.svelte';
	import FilterBar from '$lib/components/FilterBar.svelte';
	import CardTable from '$lib/components/CardTable.svelte';

	let { data } = $props();

	const stats = $derived(data.stats);
	const cards = $derived(data.cards);
	const briefing = $derived(data.briefing);

	let search = $state('');
	let sourceFilter = $state('');
	let kindFilter = $state('');
	let tagFilter = $state('');

	const sources = $derived([...new Set(cards.map((c) => c.source))].sort());
	const kinds = $derived([...new Set(cards.map((c) => c.kind))].sort());
	const allTags = $derived([...new Set(cards.flatMap((c) => c.tags))].sort());

	const filteredCards = $derived.by(() => {
		const q = search.toLowerCase();
		return cards.filter((c) => {
			if (q && !c.title.toLowerCase().includes(q) && !String(c.meta.repo).toLowerCase().includes(q))
				return false;
			if (sourceFilter && c.source !== sourceFilter) return false;
			if (kindFilter && c.kind !== kindFilter) return false;
			if (tagFilter && !c.tags.includes(tagFilter)) return false;
			return true;
		});
	});
</script>

<svelte:head>
	<title>hub</title>
</svelte:head>

<div class="px-6 py-8 max-w-screen-xl mx-auto">
	<StatBar
		entries={[
			{ value: stats.tracked, label: 'tracked' },
			{ value: stats.open, label: 'open' },
			{ value: stats.with_reactions, label: 'with reactions' },
			{ value: stats.repos, label: 'repos' }
		]}
	/>

	{#if briefing}
		<div class="mt-8">
			<Briefing {briefing} {cards} />
		</div>
	{/if}

	<div class="mt-10">
		<FilterBar
			bind:search
			bind:sourceFilter
			bind:kindFilter
			bind:tagFilter
			{sources}
			{kinds}
			tags={allTags}
			count={filteredCards.length}
		/>
		<CardTable cards={filteredCards} />
	</div>
</div>
