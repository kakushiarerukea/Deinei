<script lang="ts">
	import { serverStore } from '$lib/stores/server.svelte';

	interface Props {
		isEmpty: boolean;
	}

	let { isEmpty = false }: Props = $props();
</script>

<div
	class={[
		'pointer-events-none mb-4 hidden px-4 text-center text-balance',
		isEmpty && 'mb-[calc(50dvh-12rem)] md:mb-6 pointer-events-auto block!'
	]}
>
	<h1 class="hina-mincho-title mb-4 tracking-tight">泥濘</h1>

	<p class="text-muted-foreground md:text-lg">
		{serverStore.props?.modalities?.audio ? 'Record audio, type a message ' : 'Type a message'} or upload
		files to get started
	</p>
</div>

<style>
	@font-face {
		font-family: 'Hina Mincho';
		src: url('/fonts/HinaMincho-Regular.ttf') format('truetype');
		font-weight: normal;
		font-style: normal;
		font-display: swap;
	}

	.hina-mincho-title {
		font-family: 'Hina Mincho', serif;
		font-size: clamp(1.3rem, 2.75vw, 3.75rem);
		line-height: 1.1;
		letter-spacing: 0.05em;
	}
</style>
