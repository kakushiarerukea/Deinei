<script lang="ts">
	import dLogo from '$lib/assets/DLogo.png';
	import lLogo from '$lib/assets/LLogo.png';

	let { class: className = '', style = '' } = $props();
</script>

<span class="inline-flex items-center justify-center shrink-0 {className}" {style}>
	<img src={dLogo} alt="泥濘 Logo (Dark)" class="hidden dark:block w-full h-full object-contain pointer-events-none select-none" />
	<img src={lLogo} alt="泥濘 Logo (Light)" class="block dark:hidden w-full h-full object-contain pointer-events-none select-none" />
</span>


