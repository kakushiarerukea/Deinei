<script lang="ts">
	import { untrack } from 'svelte';
	import { PROCESSING_INFO_TIMEOUT } from '$lib/constants';
	import { useProcessingState } from '$lib/hooks/use-processing-state.svelte';
	import { chatStore, isLoading, isChatStreaming } from '$lib/stores/chat.svelte';
	import { activeMessages, activeConversation } from '$lib/stores/conversations.svelte';
	import { config } from '$lib/stores/settings.svelte';
	import ChatScreenContextGauge from './ChatScreenContextGauge.svelte';

	const processingState = useProcessingState();

	let isCurrentConversationLoading = $derived(isLoading());
	let isStreaming = $derived(isChatStreaming());
	let processingDetails = $derived(processingState.getTechnicalDetails());

	let processingVisible = $derived(processingDetails.length > 0 || !!activeConversation());

	let { onVisibilityChange }: { onVisibilityChange?: (visible: boolean) => void } = $props();

	$effect(() => {
		onVisibilityChange?.(processingVisible);
	});

	$effect(() => {
		const conversation = activeConversation();

		untrack(() => chatStore.setActiveProcessingConversation(conversation?.id ?? null));
	});

	$effect(() => {
		const keepStatsVisible = config().keepStatsVisible;
		const shouldMonitor = keepStatsVisible || isCurrentConversationLoading || isStreaming;

		if (shouldMonitor) {
			processingState.startMonitoring();
		}

		if (!isCurrentConversationLoading && !isStreaming && !keepStatsVisible) {
			const timeout = setTimeout(() => {
				if (!config().keepStatsVisible && !isChatStreaming()) {
					processingState.stopMonitoring();
				}
			}, PROCESSING_INFO_TIMEOUT);

			return () => clearTimeout(timeout);
		}
	});

	$effect(() => {
		const conversation = activeConversation();
		const messages = activeMessages() as DatabaseMessage[];
		const keepStatsVisible = config().keepStatsVisible;

		if (keepStatsVisible && conversation) {
			if (messages.length === 0) {
				untrack(() => chatStore.clearProcessingState(conversation.id));
				return;
			}

			if (!isCurrentConversationLoading && !isStreaming) {
				untrack(() => chatStore.restoreProcessingStateFromMessages(messages, conversation.id));
			}
		}
	});
</script>

<div
	class={[
		'chat-processing-info-container pointer-events-none relative w-full hidden md:block',
		processingVisible && 'visible'
	]}
>
	<div class="chat-processing-info-content pointer-events-auto backdrop-blur-sm bg-background/60 border border-border/40 rounded-lg p-2 shadow-lg">
		<ChatScreenContextGauge
			contextTotal={processingState.processingState?.contextTotal || 16384}
			contextUsed={processingState.processingState?.contextUsed || 0}
			outputTokensUsed={processingState.processingState?.outputTokensUsed || 0}
			outputTokensMax={processingState.processingState?.outputTokensMax || -1}
			tokensPerSecond={processingState.processingState?.tokensPerSecond || 0}
		/>
	</div>
</div>

<style>
	.chat-processing-info-container {
		position: sticky;
		top: 0;
		z-index: 10;
		padding: 0 1rem 0.5rem;
		opacity: 0;
		transform: translateY(20%);
		transition:
			opacity 300ms ease-out,
			transform 300ms ease-out;
	}

	.chat-processing-info-container.visible {
		opacity: 1;
		transform: translateY(0);
	}

	.chat-processing-info-content {
		width: 100%;
		max-width: 52rem;
		margin: 0 auto;
	}
</style>
