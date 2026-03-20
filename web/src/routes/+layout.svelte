<script>
	import '../app.css';
	let { children } = $props();
</script>

<div class="bg-gray-950 text-gray-100 min-h-screen">
	<nav class="px-6 py-4 border-b border-gray-800/50">
		<h1 class="text-xl font-light tracking-widest text-gray-500 lowercase">hub</h1>
	</nav>
	{@render children()}
</div>
