<script lang="ts">
	let { kind }: { kind: string } = $props();

	const colorMap: Record<string, string> = {
		pr: 'bg-violet-900/60 text-violet-300 border-violet-700/40',
		issue: 'bg-emerald-900/60 text-emerald-300 border-emerald-700/40',
		thread: 'bg-sky-900/60 text-sky-300 border-sky-700/40',
		ticket: 'bg-amber-900/60 text-amber-300 border-amber-700/40',
		github: 'bg-gray-800/60 text-gray-300 border-gray-600/40',
		tangled: 'bg-teal-900/60 text-teal-300 border-teal-700/40',
		slack: 'bg-purple-900/60 text-purple-300 border-purple-700/40',
		agent: 'bg-blue-900/60 text-blue-300 border-blue-700/40'
	};

	const fallback = 'bg-gray-900/60 text-gray-300 border-gray-700/40';

	let colors = $derived(colorMap[kind] ?? fallback);
</script>

<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border {colors}">
	{#if kind === 'github'}
		<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
			<path fill-rule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
		</svg>
	{:else if kind === 'tangled'}
		<img src="https://cdn.bsky.app/img/avatar/plain/did:plc:wshs7t2adsemcrrd4snkeqli/bafkreif6z53z4ukqmdgwstspwh5asmhxheblcd2adisoccl4fflozc3kva@jpeg" alt="" width="12" height="12" class="rounded-full" aria-hidden="true" />
	{/if}
	{kind.toLowerCase()}
</span>
