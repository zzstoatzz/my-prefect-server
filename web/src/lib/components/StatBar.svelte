<script lang="ts">
	interface Entry {
		value: number;
		label: string;
	}

	let { entries }: { entries: Entry[] } = $props();
</script>

<div class="grid grid-cols-2 sm:grid-cols-4 gap-4">
	{#each entries as entry (entry.label)}
		<div class="bg-gray-800 rounded-lg px-5 py-4">
			<p class="text-3xl font-semibold text-gray-100">{entry.value}</p>
			<p class="text-xs text-gray-400 mt-1 uppercase tracking-wider">{entry.label}</p>
		</div>
	{/each}
</div>
