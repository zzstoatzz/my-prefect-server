<script>
	import '../app.css';
	let { children } = $props();
</script>

<div class="bg-gray-950 text-gray-100 min-h-screen">
	{@render children()}
</div>
