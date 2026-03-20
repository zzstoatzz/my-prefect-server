<script>
	let { data } = $props();

	const stats = $derived(data.stats);
	const items = $derived(data.items);
</script>

<svelte:head>
	<title>hub</title>
</svelte:head>

<div class="px-6 py-8 max-w-screen-xl mx-auto">
	<header class="mb-8">
		<h1 class="text-xl font-light tracking-widest text-gray-500 lowercase">hub</h1>
	</header>

	<!-- stat tiles -->
	<div class="grid grid-cols-4 gap-4 mb-10">
		<div class="bg-gray-800 rounded-lg px-5 py-4">
			<p class="text-3xl font-semibold text-gray-100">{stats.tracked}</p>
			<p class="text-xs text-gray-400 mt-1 uppercase tracking-wider">tracked</p>
		</div>
		<div class="bg-gray-800 rounded-lg px-5 py-4">
			<p class="text-3xl font-semibold text-gray-100">{stats.open}</p>
			<p class="text-xs text-gray-400 mt-1 uppercase tracking-wider">open</p>
		</div>
		<div class="bg-gray-800 rounded-lg px-5 py-4">
			<p class="text-3xl font-semibold text-gray-100">{stats.with_reactions}</p>
			<p class="text-xs text-gray-400 mt-1 uppercase tracking-wider">with reactions</p>
		</div>
		<div class="bg-gray-800 rounded-lg px-5 py-4">
			<p class="text-3xl font-semibold text-gray-100">{stats.repos}</p>
			<p class="text-xs text-gray-400 mt-1 uppercase tracking-wider">repos</p>
		</div>
	</div>

	<!-- action items table -->
	<div class="bg-gray-900 rounded-lg overflow-hidden">
		<table class="w-full text-sm">
			<thead>
				<tr class="border-b border-gray-800">
					<th class="text-left px-4 py-3 text-gray-400 font-normal">repo</th>
					<th class="text-left px-4 py-3 text-gray-400 font-normal">#</th>
					<th class="text-left px-4 py-3 text-gray-400 font-normal">type</th>
					<th class="text-left px-4 py-3 text-gray-400 font-normal">title</th>
					<th class="text-left px-4 py-3 text-gray-400 font-normal">user</th>
					<th class="text-left px-4 py-3 text-gray-400 font-normal">labels</th>
					<th class="text-right px-4 py-3 text-gray-400 font-normal">score</th>
					<th class="text-right px-4 py-3 text-gray-400 font-normal">updated</th>
				</tr>
			</thead>
			<tbody>
				{#each items as item (item.repo + '#' + item.number)}
					<tr class="border-b border-gray-800/50 hover:bg-gray-800/60 transition-colors">
						<td class="px-4 py-3 text-gray-400 font-mono text-xs whitespace-nowrap">
							{item.repo}
						</td>
						<td class="px-4 py-3 text-gray-500 font-mono text-xs whitespace-nowrap">
							{item.number}
						</td>
						<td class="px-4 py-3 whitespace-nowrap">
							{#if item.type === 'pr'}
								<span class="inline-block px-2 py-0.5 rounded-full text-xs bg-violet-900/60 text-violet-300 border border-violet-700/40">
									pr
								</span>
							{:else}
								<span class="inline-block px-2 py-0.5 rounded-full text-xs bg-emerald-900/60 text-emerald-300 border border-emerald-700/40">
									issue
								</span>
							{/if}
						</td>
						<td class="px-4 py-3 max-w-xs">
							<a
								href={item.url}
								target="_blank"
								rel="noopener noreferrer"
								class="text-gray-200 hover:text-white hover:underline truncate block"
							>
								{item.title}
							</a>
						</td>
						<td class="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">
							{item.user}
						</td>
						<td class="px-4 py-3 text-gray-500 text-xs">
							{item.labels}
						</td>
						<td class="px-4 py-3 text-right text-gray-300 font-mono text-xs whitespace-nowrap">
							{item.importance_score.toFixed(1)}
						</td>
						<td class="px-4 py-3 text-right text-gray-500 font-mono text-xs whitespace-nowrap">
							{item.updated_at.slice(0, 10)}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>
