<script lang="ts">
	let {
		search = $bindable(''),
		sourceFilter = $bindable(''),
		kindFilter = $bindable(''),
		tagFilter = $bindable(''),
		sources,
		kinds,
		tags,
		count
	}: {
		search: string;
		sourceFilter: string;
		kindFilter: string;
		tagFilter: string;
		sources: string[];
		kinds: string[];
		tags: string[];
		count: number;
	} = $props();
</script>

<div class="flex flex-row gap-3 items-center flex-wrap mb-6">
	<input
		bind:value={search}
		placeholder="search..."
		class="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-gray-500 w-64"
	/>

	<select
		bind:value={sourceFilter}
		class="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-gray-500 w-auto"
	>
		<option value="">all sources</option>
		{#each sources as source (source)}
			<option value={source}>{source}</option>
		{/each}
	</select>

	<select
		bind:value={kindFilter}
		class="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-gray-500 w-auto"
	>
		<option value="">all kinds</option>
		{#each kinds as kind (kind)}
			<option value={kind}>{kind}</option>
		{/each}
	</select>

	<select
		bind:value={tagFilter}
		class="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-gray-500 w-auto"
	>
		<option value="">all tags</option>
		{#each tags as tag (tag)}
			<option value={tag}>{tag}</option>
		{/each}
	</select>

	<span class="text-xs text-gray-500 ml-auto">{count} items</span>
</div>
