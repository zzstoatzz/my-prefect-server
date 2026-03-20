<script lang="ts">
	let { kind }: { kind: string } = $props();

	const colorMap: Record<string, string> = {
		pr: 'bg-violet-900/60 text-violet-300 border-violet-700/40',
		issue: 'bg-emerald-900/60 text-emerald-300 border-emerald-700/40',
		thread: 'bg-sky-900/60 text-sky-300 border-sky-700/40',
		ticket: 'bg-amber-900/60 text-amber-300 border-amber-700/40',
		github: 'bg-gray-800/60 text-gray-300 border-gray-600/40',
		slack: 'bg-purple-900/60 text-purple-300 border-purple-700/40',
		agent: 'bg-blue-900/60 text-blue-300 border-blue-700/40'
	};

	const fallback = 'bg-gray-900/60 text-gray-300 border-gray-700/40';

	let colors = $derived(colorMap[kind] ?? fallback);
</script>

<span class="inline-block px-2 py-0.5 rounded-full text-xs border {colors}">
	{kind.toLowerCase()}
</span>
