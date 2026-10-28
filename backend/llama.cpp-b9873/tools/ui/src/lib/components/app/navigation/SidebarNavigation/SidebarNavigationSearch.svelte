<script lang="ts">
	import { SearchInput } from '$lib/components/app';
	import { t } from '$lib/stores/i18n.svelte';

	interface Props {
		value?: string;
		placeholder?: string;
		onInput?: (value: string) => void;
		class?: string;
	}

	let {
		value = $bindable(''),
		placeholder = t('sidebar.searchPlaceholder', '会話を検索...'),
		onInput,
		class: className
	}: Props = $props();
</script>

<div class="mb-4 px-2 {className}">
	<SearchInput bind:value {placeholder} {onInput} />
</div>
