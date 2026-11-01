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

<div class="flex flex-row gap-3 items-center flex-wrap mb-6" role="search">
	<input
		bind:value={search}
		placeholder="search..."
		aria-label="search items"
		class="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-gray-500 focus-visible:ring-2 focus-visible:ring-indigo-500/50 w-full sm:w-64"
	/>

	<select
		bind:value={sourceFilter}
		aria-label="filter by source"
		class="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-gray-500 focus-visible:ring-2 focus-visible:ring-indigo-500/50 flex-1 sm:flex-none min-w-0"
	>
		<option value="">all sources</option>
		{#each sources as source (source)}
			<option value={source}>{source}</option>
		{/each}
	</select>

	<select
		bind:value={kindFilter}
		aria-label="filter by kind"
		class="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-gray-500 focus-visible:ring-2 focus-visible:ring-indigo-500/50 flex-1 sm:flex-none min-w-0"
	>
		<option value="">all kinds</option>
		{#each kinds as kind (kind)}
			<option value={kind}>{kind}</option>
		{/each}
	</select>

	<select
		bind:value={tagFilter}
		aria-label="filter by tag"
		class="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-gray-500 focus-visible:ring-2 focus-visible:ring-indigo-500/50 flex-1 sm:flex-none min-w-0"
	>
		<option value="">all tags</option>
		{#each tags as tag (tag)}
			<option value={tag}>{tag}</option>
		{/each}
	</select>

	<span class="text-xs text-gray-500 ml-auto" aria-live="polite">{count} items</span>
</div>
