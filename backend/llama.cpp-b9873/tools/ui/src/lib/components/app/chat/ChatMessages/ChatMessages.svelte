<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { beforeNavigate, afterNavigate } from '$app/navigation';
	import { ChatMessage, ChatMessageUserPending } from '$lib/components/app';
	import { setChatActionsContext } from '$lib/contexts';
	import { MessageRole } from '$lib/enums';
	import { chatStore } from '$lib/stores/chat.svelte';
	import {
		chatPendingMessageContent,
		chatPendingMessageExtras,
		chatClearPendingMessage,
		chatInjectPendingMessage
	} from '$lib/stores/chat.svelte';
	import { conversationsStore, activeConversation } from '$lib/stores/conversations.svelte';
	import { config } from '$lib/stores/settings.svelte';
	import {
		agenticPendingSteeringMessageContent,
		agenticPendingSteeringMessageExtras,
		agenticClearSteeringMessage,
		agenticInjectSteeringMessage
	} from '$lib/stores/agentic.svelte';
	import {
		buildSiblingInfoMap,
		copyToClipboard,
		formatMessageForClipboard,
		hasAgenticContent
	} from '$lib/utils';

	interface Props {
		messages?: DatabaseMessage[];
		onUserAction?: () => void;
		onMessagesReady?: (messageCount: number) => void;
	}

	let { messages = [], onUserAction, onMessagesReady }: Props = $props();

	const INITIAL_VISIBLE_MESSAGES = 60;
	const LOAD_MORE_STEP = 60;

	let allConversationMessages = $state<DatabaseMessage[]>([]);
	let isVisible = $state(false);
	let previousConversationId = $state<string | null>(null);
	let previousRouteId = $state<string | null>(null);
	let visibleStartIndex = $state<number | null>(null);
	let lastRefreshedConvId: string | null = null;
	let lastRefreshedMsgCount = -1;

	const currentConfig = config();

	setChatActionsContext({
		copy: async (message: DatabaseMessage) => {
			const asPlainText = Boolean(currentConfig.copyTextAttachmentsAsPlainText);
			const clipboardContent = formatMessageForClipboard(
				message.content,
				message.extra,
				asPlainText
			);
			await copyToClipboard(clipboardContent, 'Message copied to clipboard');
		},

		delete: async (message: DatabaseMessage) => {
			await chatStore.deleteMessage(message.id);
			refreshAllMessages();
		},

		navigateToSibling: async (siblingId: string) => {
			await conversationsStore.navigateToSibling(siblingId);
			refreshAllMessages();
		},

		editWithBranching: async (
			message: DatabaseMessage,
			newContent: string,
			newExtras?: DatabaseMessageExtra[]
		) => {
			onUserAction?.();
			await chatStore.editMessageWithBranching(message.id, newContent, newExtras);
			refreshAllMessages();
		},

		editWithReplacement: async (
			message: DatabaseMessage,
			newContent: string,
			shouldBranch: boolean
		) => {
			onUserAction?.();
			await chatStore.editAssistantMessage(message.id, newContent, shouldBranch);
			refreshAllMessages();
		},

		editUserMessagePreserveResponses: async (
			message: DatabaseMessage,
			newContent: string,
			newExtras?: DatabaseMessageExtra[]
		) => {
			onUserAction?.();
			await chatStore.editUserMessagePreserveResponses(message.id, newContent, newExtras);
			refreshAllMessages();
		},

		regenerateWithBranching: async (message: DatabaseMessage, modelOverride?: string) => {
			onUserAction?.();
			await chatStore.regenerateMessageWithBranching(message.id, modelOverride);
			refreshAllMessages();
		},

		continueAssistantMessage: async (message: DatabaseMessage) => {
			onUserAction?.();
			await chatStore.continueAssistantMessage(message.id);
			refreshAllMessages();
		},

		forkConversation: async (
			message: DatabaseMessage,
			options: { name: string; includeAttachments: boolean }
		) => {
			await conversationsStore.forkConversation(message.id, options);
		}
	});

	function refreshAllMessages() {
		const conversation = activeConversation();

		if (conversation) {
			conversationsStore.getConversationMessages(conversation.id).then((messages) => {
				allConversationMessages = messages;
			});
		} else {
			allConversationMessages = [];
		}
	}

	// Track conversation changes to trigger transition even on same route
	$effect(() => {
		const conversation = activeConversation();
		const currentId = conversation?.id ?? null;
		const currentMsgCount = messages.length;

		if (currentId !== previousConversationId && previousConversationId !== null) {
			// Conversation changed - trigger fade out/in
			isVisible = false;
			visibleStartIndex = null;
			lastRefreshedConvId = currentId;
			lastRefreshedMsgCount = currentMsgCount;
			requestAnimationFrame(() => {
				refreshAllMessages();
				previousConversationId = currentId;
				requestAnimationFrame(() => {
					isVisible = true;
				});
			});
		} else {
			if (currentId !== previousConversationId) {
				visibleStartIndex = null;
				previousConversationId = currentId;
			}
			if (conversation && (currentId !== lastRefreshedConvId || currentMsgCount !== lastRefreshedMsgCount)) {
				lastRefreshedConvId = currentId;
				lastRefreshedMsgCount = currentMsgCount;
				refreshAllMessages();
			}
		}
	});

	$effect(() => {
		void allConversationMessages;

		onMessagesReady?.(displayMessages.length);
	});

	onMount(() => {
		requestAnimationFrame(() => {
			isVisible = true;
		});
	});

	beforeNavigate((navigation) => {
		isVisible = false;
		previousRouteId = navigation.from?.route.id ?? null;
	});

	afterNavigate(() => {
		requestAnimationFrame(() => {
			isVisible = true;
		});
	});

	let siblingInfoByMessageId = $derived(buildSiblingInfoMap(allConversationMessages));

	let displayMessages = $derived.by(() => {
		if (!messages.length) {
			return [];
		}

		const filteredMessages = currentConfig.showSystemMessage
			? messages
			: messages.filter((msg) => msg.type !== MessageRole.SYSTEM);

		// Build display entries, grouping agentic sessions into single entries.
		// An agentic session = assistant(with tool_calls) → tool → assistant → tool → ... → assistant(final)
		const result: Array<{
			message: DatabaseMessage;
			toolMessages: DatabaseMessage[];
			isLastAssistantMessage: boolean;
			siblingInfo: ChatMessageSiblingInfo;
		}> = [];

		for (let i = 0; i < filteredMessages.length; i++) {
			const msg = filteredMessages[i];

			// Skip tool messages - they're grouped with preceding assistant
			if (msg.role === MessageRole.TOOL) continue;

			const toolMessages: DatabaseMessage[] = [];
			if (msg.role === MessageRole.ASSISTANT && hasAgenticContent(msg)) {
				let j = i + 1;

				while (j < filteredMessages.length) {
					const next = filteredMessages[j];

					if (next.role === MessageRole.TOOL) {
						toolMessages.push(next);

						j++;
					} else if (next.role === MessageRole.ASSISTANT) {
						toolMessages.push(next);

						j++;
					} else {
						break;
					}
				}

				i = j - 1;
			} else if (msg.role === MessageRole.ASSISTANT) {
				let j = i + 1;

				while (j < filteredMessages.length && filteredMessages[j].role === MessageRole.TOOL) {
					toolMessages.push(filteredMessages[j]);
					j++;
				}
			}

			const siblingInfo = siblingInfoByMessageId.get(msg.id) ?? {
				message: msg,
				siblingIds: [msg.id],
				currentIndex: 0,
				totalSiblings: 1
			};

			result.push({
				message: msg,
				toolMessages,
				isLastAssistantMessage: false,
				siblingInfo
			});
		}

		// Mark the last assistant message
		for (let i = result.length - 1; i >= 0; i--) {
			if (result[i].message.role === MessageRole.ASSISTANT) {
				result[i].isLastAssistantMessage = true;
				break;
			}
		}

		return result;
	});

	$effect(() => {
		const total = displayMessages.length;
		if (total === 0) {
			visibleStartIndex = null;
		} else if (visibleStartIndex === null || visibleStartIndex >= total) {
			visibleStartIndex = Math.max(0, total - INITIAL_VISIBLE_MESSAGES);
		}
	});

	let effectiveStartIndex = $derived.by(() => {
		const total = displayMessages.length;
		if (total <= INITIAL_VISIBLE_MESSAGES) return 0;
		if (visibleStartIndex === null || visibleStartIndex >= total) {
			return Math.max(0, total - INITIAL_VISIBLE_MESSAGES);
		}
		return Math.max(0, visibleStartIndex);
	});

	let visibleDisplayMessages = $derived(
		effectiveStartIndex > 0 ? displayMessages.slice(effectiveStartIndex) : displayMessages
	);

	async function handleLoadOlderMessages(loadAll = false) {
		const scrollEl = document.querySelector('.chat-screen > .overflow-y-auto') as HTMLElement | null;
		const useWindowScroll = !scrollEl || getComputedStyle(scrollEl).overflowY === 'visible';
		const prevHeight = useWindowScroll ? document.documentElement.scrollHeight : scrollEl.scrollHeight;

		if (loadAll) {
			visibleStartIndex = 0;
		} else {
			visibleStartIndex = Math.max(0, effectiveStartIndex - LOAD_MORE_STEP);
		}

		await tick();
		const nextHeight = useWindowScroll ? document.documentElement.scrollHeight : scrollEl.scrollHeight;
		const heightDiff = nextHeight - prevHeight;
		if (heightDiff > 0) {
			if (useWindowScroll) {
				window.scrollBy({ top: heightDiff, behavior: 'instant' as ScrollBehavior });
			} else if (scrollEl) {
				scrollEl.scrollTop += heightDiff;
			}
		}
	}
</script>

<div
	class="transition-opacity duration-500 ease-out
		{isVisible ? 'opacity-100' : 'opacity-0'}
		{previousRouteId === '/(chat)/chat/[id]' ? '' : 'delay-300'}"
>
	{#if effectiveStartIndex > 0}
		<div class="mx-auto mt-2 mb-4 flex flex-wrap items-center justify-center gap-2 w-full max-w-3xl">
			<button
				type="button"
				onclick={() => handleLoadOlderMessages(false)}
				class="px-3.5 py-1.5 text-xs font-medium rounded-full bg-muted/60 hover:bg-muted text-muted-foreground hover:text-foreground border border-border/40 transition-colors cursor-pointer"
			>
				過去のメッセージを表示 (+{Math.min(LOAD_MORE_STEP, effectiveStartIndex)}件 / 残り{effectiveStartIndex}件)
			</button>
			{#if effectiveStartIndex > LOAD_MORE_STEP}
				<button
					type="button"
					onclick={() => handleLoadOlderMessages(true)}
					class="px-3 py-1.5 text-xs font-medium rounded-full bg-muted/40 hover:bg-muted/80 text-muted-foreground/80 hover:text-foreground border border-border/30 transition-colors cursor-pointer"
				>
					すべて表示
				</button>
			{/if}
		</div>
	{/if}

	{#each visibleDisplayMessages as { message, toolMessages, isLastAssistantMessage, siblingInfo } (message.id)}
		<ChatMessage
			class="mx-auto mt-4 md:mt-12 first:mt-1 w-full max-w-3xl"
			{message}
			{toolMessages}
			{isLastAssistantMessage}
			{siblingInfo}
		/>
	{/each}

	{#if activeConversation() && agenticPendingSteeringMessageContent(activeConversation()!.id)}
		{@const convId = activeConversation()!.id}
		{@const pendingContent = agenticPendingSteeringMessageContent(convId)}

		{#if pendingContent}
			<ChatMessageUserPending
				class="mx-auto mt-4 md:mt-12 first:mt-1 w-full max-w-[48rem]"
				content={pendingContent}
				extras={agenticPendingSteeringMessageExtras(convId)}
				onSendImmediately={() => chatStore.abortCurrentFlow(convId)}
				onEdit={(newContent, extras) => agenticInjectSteeringMessage(convId, newContent, extras)}
				onDelete={() => agenticClearSteeringMessage(convId)}
			/>
		{/if}
	{:else if activeConversation() && chatPendingMessageContent(activeConversation()!.id)}
		{@const convId = activeConversation()!.id}
		{@const pendingContent = chatPendingMessageContent(convId)}

		{#if pendingContent}
			<ChatMessageUserPending
				class="mx-auto mt-4 md:mt-12 first:mt-1 w-full max-w-[48rem]"
				content={pendingContent}
				extras={chatPendingMessageExtras(convId)}
				onSendImmediately={() => chatStore.abortCurrentFlow(convId)}
				onEdit={(newContent, extras) => chatInjectPendingMessage(convId, newContent, extras)}
				onDelete={() => chatClearPendingMessage(convId)}
			/>
		{/if}
	{/if}
</div>
