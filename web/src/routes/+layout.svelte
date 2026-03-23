<script>
	import '../app.css';
	let { children } = $props();
</script>

<div class="bg-gray-950 text-gray-100 min-h-screen">
	<header class="border-b border-gray-800/50">
		<nav aria-label="main" class="max-w-screen-xl mx-auto flex items-center justify-between px-6 py-4">
			<a href="/" class="text-xl font-light tracking-widest text-gray-500 lowercase hover:text-gray-300 focus-visible:text-gray-300 transition-colors">hub</a>
			<div class="flex items-center gap-1">
				<a
					href="https://prefect-server.waow.tech"
					target="_blank"
					rel="noopener noreferrer"
					class="p-2 text-gray-500 hover:text-gray-300 hover:bg-gray-800/60 rounded-lg transition-colors"
					aria-label="prefect server"
				>
					<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
						<rect x="2" y="3" width="20" height="6" rx="1" />
						<rect x="2" y="15" width="20" height="6" rx="1" />
						<path d="M12 9v6" />
						<path d="M6 9v3a3 3 0 0 0 3 3" />
						<path d="M18 9v3a3 3 0 0 1-3 3" />
					</svg>
				</a>
				<a
					href="https://tangled.org/zzstoatzz.io/my-prefect-server"
					target="_blank"
					rel="noopener noreferrer"
					class="p-2 text-gray-500 hover:text-gray-300 hover:bg-gray-800/60 rounded-lg transition-colors"
					aria-label="source code"
				>
					<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
						<polyline points="16 18 22 12 16 6" />
						<polyline points="8 6 2 12 8 18" />
					</svg>
				</a>
			</div>
		</nav>
	</header>
	<main class="max-w-screen-xl mx-auto">
		{@render children()}
	</main>
</div>
