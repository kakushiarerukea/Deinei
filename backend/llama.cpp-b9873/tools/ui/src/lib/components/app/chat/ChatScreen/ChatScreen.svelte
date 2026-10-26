<script lang="ts">
	import { page } from '$app/state';
	import {
		ChatScreenForm,
		ChatMessages,
		ChatScreenDragOverlay,
		ChatScreenProcessingInfo,
		ChatScreenStreamResumeStatus,
		ServerLoadingSplash,
		ChatScreenServerError
	} from '$lib/components/app';
	import { setProcessingInfoContext } from '$lib/contexts';
	import { createAutoScrollController } from '$lib/hooks/use-auto-scroll.svelte';
	import { useChatScreenActiveModel } from '$lib/hooks/use-chat-screen-active-model.svelte';
	import { useChatScreenDragAndDrop } from '$lib/hooks/use-chat-screen-drag-and-drop.svelte';
	import { useChatScreenFileUpload } from '$lib/hooks/use-chat-screen-file-upload.svelte';
	import { useChatScreenScroll } from '$lib/hooks/use-chat-screen-scroll.svelte';
	import { useKeyboardShortcuts } from '$lib/hooks/use-keyboard-shortcuts.svelte';
	import { device } from '$lib/stores/device.svelte';
	import { isMobile } from '$lib/stores/viewport.svelte';
	import {
		chatStore,
		errorDialog,
		isLoading,
		isChatStreaming,
		isEditing,
		activeProcessingState
	} from '$lib/stores/chat.svelte';
	import {
		conversationsStore,
		activeMessages,
		activeConversation
	} from '$lib/stores/conversations.svelte';
	import { config } from '$lib/stores/settings.svelte';
	import { serverLoading, serverError } from '$lib/stores/server.svelte';
	import { ttsStore } from '$lib/stores/tts.svelte';
	import { parseFilesToMessageExtras } from '$lib/utils/browser-only';
	import { onDestroy, onMount } from 'svelte';
	import ChatScreenGreeting from './ChatScreenGreeting.svelte';
	import ChatScreenActionScrollDown from './ChatScreenActionScrollDown.svelte';
	import ChatScreenDialogsAndAlerts from './ChatScreenDialogsAndAlerts.svelte';
	import { ROUTES } from '$lib/constants';

	let { showCenteredEmpty = false } = $props();

	setProcessingInfoContext({
		get showProcessingInfo() {
			return showProcessingInfo;
		}
	});

	let disableAutoScroll = $derived(Boolean(config().disableAutoScroll) || isMobile.current);
	let isMobileUserScrolledUp = $state(false);
	let mobileScrollDownHint = $state(false);
	let mobileScrollDownHintLockedUntil = $state(0);
	let emptyFileNames = $state<string[]>([]);
	let initialMessage = $state('');
	let showDeleteDialog = $state(false);
	let showEmptyFileDialog = $state(false);
	let isEmpty = $derived(
		showCenteredEmpty && !activeConversation() && activeMessages().length === 0 && !isLoading()
	);
	let activeErrorDialog = $derived(errorDialog());
	let isServerLoading = $derived(serverLoading());
	let hasPropsError = $derived(!!serverError());
	let isCurrentConversationLoading = $derived(isLoading() || isChatStreaming());
	let isRebuildingActive = $derived(
		chatStore.rebuildingConversationId === activeConversation()?.id
	);
	let showProcessingInfo = $derived(
		isCurrentConversationLoading ||
			(config().keepStatsVisible && !!page.params.id) ||
			activeProcessingState() !== null
	);
	let chatFormBottomPosition = $derived.by(() => {
		if (!isMobile.current) return '1rem';
		if (device.isStandalone) return '1.5rem';
		if (device.isIOSSafari) return '0.25rem';
		return '0.5rem';
	});

	const autoScroll = createAutoScrollController();
	const scroll = useChatScreenScroll(autoScroll);
	let mobileScrollContainerEl = $state<HTMLElement | null>(null);

	$effect(() => {
		if (isMobile.current && mobileScrollContainerEl) {
			scroll.setScrollContainer(mobileScrollContainerEl);
		} else {
			scroll.setScrollContainer(null);
		}
	});

	const activeModel = useChatScreenActiveModel();
	const fileUpload = useChatScreenFileUpload({
		capabilities: () => ({
			hasVision: activeModel.hasVisionModality,
			hasAudio: activeModel.hasAudioModality,
			hasVideo: activeModel.hasVideoModality
		}),
		activeModelId: () => activeModel.activeModelId
	});
	const dragAndDrop = useChatScreenDragAndDrop({
		onDrop: fileUpload.handleFileUpload
	});
	const { handleKeydown } = useKeyboardShortcuts({
		deleteActiveConversation: () => {
			if (activeConversation()) {
				showDeleteDialog = true;
			}
		}
	});

	function handleMobileScroll() {
		if (!isMobile.current) return;

		const container = scroll.chatScrollContainer;
		if (!container) return;

		const distanceFromBottom =
			container.scrollHeight - container.clientHeight - container.scrollTop;
		isMobileUserScrolledUp = distanceFromBottom > 300;
	}

	async function handleDeleteConfirm() {
		const conversation = activeConversation();

		if (conversation) {
			await conversationsStore.deleteConversation(conversation.id);
		}

		showDeleteDialog = false;
	}

	async function handleSendMessage(message: string, files?: ChatUploadedFile[]): Promise<boolean> {
		if (ttsStore.isPlaying) return false;
		const plainFiles = files ? $state.snapshot(files) : undefined;
		const result = plainFiles
			? await parseFilesToMessageExtras(plainFiles, activeModel.activeModelId ?? undefined)
			: undefined;

		if (result?.emptyFiles && result.emptyFiles.length > 0) {
			emptyFileNames = result.emptyFiles;
			showEmptyFileDialog = true;
			if (files) {
				const emptyFileNamesSet = new Set(result.emptyFiles);
				fileUpload.uploadedFiles = fileUpload.uploadedFiles.filter(
					(file) => !emptyFileNamesSet.has(file.name)
				);
			}
			return false;
		}

		handleSendLikeScroll();

		await chatStore.sendMessage(message, result?.extras);
		return true;
	}

	function handleSendLikeScroll() {
		if (!isMobile.current) {
			autoScroll.enable();
		}

		setTimeout(() => {
			const container = scroll.chatScrollContainer;
			if (!container) return;

			const lastUserBubble = container.querySelector(
				'.chat-message:nth-last-child(2) .chat-message-user .chat-message-user-bubble'
			) as HTMLElement | null;

			if (isMobile.current) {
				// Keep the last user message bubble just above the input on mobile
				const bubbleHeight = lastUserBubble?.scrollHeight ?? 0;
				const baseHeight = container.scrollHeight - innerHeight;

				container.scrollTo({
					top: bubbleHeight > 0 ? baseHeight - bubbleHeight : baseHeight,
					behavior: 'smooth'
				});
			} else if (lastUserBubble) {
				// On desktop, place the last user message near the top of the viewport
				const topPadding = 24;
				const bubbleRect = lastUserBubble.getBoundingClientRect();
				container.scrollTo({
					top: Math.max(0, container.scrollTop + bubbleRect.top - topPadding),
					behavior: 'smooth'
				});
			} else {
				autoScroll.scrollToBottom();
			}
		}, 100);

		if (isMobile.current) {
			autoScroll.setDisabled(disableAutoScroll);
			mobileScrollDownHint = true;
			mobileScrollDownHintLockedUntil = Date.now() + 500;
		}
	}

	function handleErrorDialogOpenChange(open: boolean) {
		if (!open) {
			chatStore.dismissErrorDialog();
		}
	}

	async function handleSystemPromptAdd(draft: { message: string; files: ChatUploadedFile[] }) {
		if (draft.message || draft.files.length > 0) {
			chatStore.savePendingDraft(draft.message, draft.files);
		}
		await chatStore.addSystemPrompt();
	}

	$effect(() => {
		const shouldDisableAutoScroll =
			config().disableAutoScroll || (isMobile.current && isCurrentConversationLoading);
		autoScroll.setDisabled(shouldDisableAutoScroll);
		if (!shouldDisableAutoScroll) {
			autoScroll.enable();
		}
	});

	const DEFAULT_MOBILE_CHAT_HEIGHT_DVH = 53;
	const MIN_MOBILE_CHAT_HEIGHT_DVH = 22;
	const MAX_MOBILE_CHAT_HEIGHT_DVH = 85;
	const MOBILE_CHAT_HEIGHT_STORAGE_KEY = 'deining_mobile_chat_height_dvh';

	let mobileChatHeightDvh = $state(DEFAULT_MOBILE_CHAT_HEIGHT_DVH);
	let isDraggingDivider = $state(false);
	let dragStartY = 0;
	let dragStartHeightDvh = DEFAULT_MOBILE_CHAT_HEIGHT_DVH;
	let lastDividerTapTime = 0;

	function handleDividerPointerDown(e: PointerEvent) {
		if (!isMobile.current) return;
		e.preventDefault();
		e.stopPropagation();

		const now = Date.now();
		if (now - lastDividerTapTime < 300) {
			mobileChatHeightDvh = DEFAULT_MOBILE_CHAT_HEIGHT_DVH;
			try {
				localStorage.setItem(
					MOBILE_CHAT_HEIGHT_STORAGE_KEY,
					String(DEFAULT_MOBILE_CHAT_HEIGHT_DVH)
				);
			} catch (_) {}
			lastDividerTapTime = 0;
			return;
		}
		lastDividerTapTime = now;

		isDraggingDivider = true;
		dragStartY = e.clientY;
		dragStartHeightDvh = mobileChatHeightDvh;

		const target = e.currentTarget as HTMLElement | null;
		if (target && typeof target.setPointerCapture === 'function') {
			try {
				target.setPointerCapture(e.pointerId);
			} catch (_) {}
		}
	}

	function handleDividerPointerMove(e: PointerEvent) {
		if (!isDraggingDivider || !isMobile.current) return;
		e.preventDefault();

		const vh = window.visualViewport?.height || window.innerHeight || 1;
		const deltaY = dragStartY - e.clientY;
		const deltaDvh = (deltaY / vh) * 100;
		const nextDvh = Math.max(
			MIN_MOBILE_CHAT_HEIGHT_DVH,
			Math.min(MAX_MOBILE_CHAT_HEIGHT_DVH, Math.round((dragStartHeightDvh + deltaDvh) * 10) / 10)
		);
		mobileChatHeightDvh = nextDvh;
	}

	function handleDividerPointerUp(e: PointerEvent) {
		if (!isDraggingDivider) return;
		isDraggingDivider = false;

		const target = e.currentTarget as HTMLElement | null;
		if (target && typeof target.releasePointerCapture === 'function') {
			try {
				target.releasePointerCapture(e.pointerId);
			} catch (_) {}
		}

		try {
			localStorage.setItem(MOBILE_CHAT_HEIGHT_STORAGE_KEY, String(mobileChatHeightDvh));
		} catch (_) {}
	}

	onMount(() => {
		try {
			const savedHeight = localStorage.getItem(MOBILE_CHAT_HEIGHT_STORAGE_KEY);
			if (savedHeight !== null) {
				const parsed = parseFloat(savedHeight);
				if (!isNaN(parsed)) {
					mobileChatHeightDvh = Math.max(
						MIN_MOBILE_CHAT_HEIGHT_DVH,
						Math.min(MAX_MOBILE_CHAT_HEIGHT_DVH, parsed)
					);
				}
			}
		} catch (_) {}

		const pendingDraft = chatStore.consumePendingDraft();
		if (pendingDraft) {
			initialMessage = pendingDraft.message;
			fileUpload.uploadedFiles = pendingDraft.files;
		}

		autoScroll.startObserving();

		if (!disableAutoScroll) {
			autoScroll.enable();
		}

		if (isMobile.current && isCurrentConversationLoading) {
			mobileScrollDownHint = true;
			mobileScrollDownHintLockedUntil = Date.now() + 500;
		}

		handleMobileScroll();
	});

	onDestroy(() => autoScroll.destroy());
</script>

{#if dragAndDrop.isDragOver}
	<ChatScreenDragOverlay />
{/if}

<svelte:window
	onkeydown={handleKeydown}
	onscroll={(e) => {
		scroll.handleScroll(e);
		handleMobileScroll();
		if (e.isTrusted && Date.now() > mobileScrollDownHintLockedUntil) {
			mobileScrollDownHint = false;
		}
	}}
/>

{#if isServerLoading}
	<ServerLoadingSplash />
{:else}
	<div
		class={[
			'chat-screen',
			// モバイル: 画面下部に縮めて配置 (可変高さdvh、初期値53dvh)、レイヤーは手前 (z-10)
			'fixed bottom-0 left-0 right-0 z-10 flex flex-col pointer-events-none',
			// デスクトップ: 従来の右ペイン全画面配置
			'md:static md:flex md:grow md:flex-col md:min-h-full md:h-auto md:pointer-events-auto md:px-4 md:py-0 md:pt-16 md:pb-4'
		]}
		style:height={isMobile.current ? `${mobileChatHeightDvh}dvh` : undefined}
		style:--chat-form-bottom-position={chatFormBottomPosition}
		role="main"
	>
		<!-- モバイル用背面パネル: 不透明度90% + 上端フェード -->
		<div
			class="pointer-events-auto absolute inset-0 -z-10 bg-background/90 backdrop-blur-md mobile-panel-fade-mask md:hidden"
			aria-hidden="true"
		></div>

		<!-- モバイル用境界線ドラッグハンドル (上下スライドで高さ調整 / ダブルタップで初期値リセット) -->
		<div
			class="pointer-events-auto absolute top-0 left-0 right-0 z-20 flex h-6 items-center justify-center touch-none select-none cursor-ns-resize md:hidden"
			role="separator"
			aria-orientation="horizontal"
			aria-label="チャット領域の高さを調整（ダブルタップでリセット）"
			aria-valuenow={Math.round(mobileChatHeightDvh)}
			aria-valuemin={MIN_MOBILE_CHAT_HEIGHT_DVH}
			aria-valuemax={MAX_MOBILE_CHAT_HEIGHT_DVH}
			onpointerdown={handleDividerPointerDown}
			onpointermove={handleDividerPointerMove}
			onpointerup={handleDividerPointerUp}
			onpointercancel={handleDividerPointerUp}
		>
			<div
				class={[
					'h-1.5 rounded-full transition-all duration-150 shadow-xs',
					isDraggingDivider
						? 'w-14 bg-primary/90 scale-y-110'
						: 'w-10 bg-foreground/30 hover:bg-foreground/50'
				]}
			></div>
		</div>

		<!-- メッセージスクロール領域 (モバイル: 下部ウィンドウ内でスクロール、上端境界線をフェード) -->
		<div
			bind:this={mobileScrollContainerEl}
			class={[
				'w-full',
				// モバイル: 独立スクロール + 上端フェードマスク (上下余白をコンパクト化)
				'flex-1 min-h-0 overflow-y-auto px-3 pt-6 pb-1 pointer-events-auto mobile-chat-fade-mask overscroll-contain',
				// デスクトップ: 従来の通常フロー
				'md:flex-none md:overflow-visible md:p-0 md:[mask-image:none] md:[-webkit-mask-image:none]'
			]}
			onscroll={(e) => {
				if (isMobile.current) {
					scroll.handleScroll(e);
					handleMobileScroll();
					if (e.isTrusted && Date.now() > mobileScrollDownHintLockedUntil) {
						mobileScrollDownHint = false;
					}
				}
			}}
			ondragenter={dragAndDrop.dragHandlers.dragenter}
			ondragleave={dragAndDrop.dragHandlers.dragleave}
			ondragover={dragAndDrop.dragHandlers.dragover}
			ondrop={dragAndDrop.dragHandlers.drop}
		>
			{#if !isEmpty}
				<div class="w-full">
					<ChatMessages
						messages={activeMessages()}
						onUserAction={() => {
							handleSendLikeScroll();
						}}
					/>
				</div>
			{/if}
		</div>

		<!-- チャットフォーム（入力欄＋コンテキストゲージ＋ステータス） -->
		<div
			class={[
				'w-full pointer-events-auto relative',
				// モバイル: 最下部に密着（セーフエリア・システムナビゲーションバーの重なり防止）
				'shrink-0 px-2 pb-[calc(0.5rem+env(safe-area-inset-bottom,0px))]',
				// デスクトップ: 従来の固定/スティッキー配置
				'md:sticky md:bottom-4 md:mt-auto md:px-0 md:pb-0 md:pointer-events-none',
				isEmpty ? 'md:bottom-[calc(50dvh-7rem)] 2xl:bottom-[calc(50dvh-4rem)]' : 'md:bottom-4'
			]}
			style:padding-top={!isEmpty && !isMobile.current ? 'var(--chat-form-padding-top)' : undefined}
		>
			<ChatScreenGreeting {isEmpty} />

			<ChatScreenServerError />

			{#if page.params.id}
				<ChatScreenStreamResumeStatus />
			{/if}

			{#if (isMobile.current ? mobileScrollDownHint || isMobileUserScrolledUp : autoScroll.userScrolledUp) && page.url.hash.includes(ROUTES.CHAT) && page.params.id}
				<div class="pointer-events-auto absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-20 md:static md:bottom-auto md:left-auto md:translate-x-0 md:mb-0 md:pointer-events-none md:flex md:flex-col md:gap-6 md:items-center md:w-full">
					<ChatScreenActionScrollDown
						onclick={() => {
							mobileScrollDownHint = false;
							scroll.chatScrollContainer?.scrollTo({
								top: scroll.chatScrollContainer.scrollHeight,
								behavior: 'smooth'
							});
						}}
					/>
				</div>
			{/if}

			<ChatScreenForm
				class="pointer-events-auto conversation-chat-form"
				disabled={hasPropsError || isEditing() || chatStore.isSummarizing || isRebuildingActive || ttsStore.isPlaying}
				{initialMessage}
				isLoading={isCurrentConversationLoading || chatStore.isSummarizing || isRebuildingActive}
				onFileRemove={fileUpload.handleFileRemove}
				onFileUpload={fileUpload.handleFileUpload}
				onSend={handleSendMessage}
				onStop={() => chatStore.stopGeneration()}
				onSystemPromptAdd={handleSystemPromptAdd}
				bind:uploadedFiles={fileUpload.uploadedFiles}
			/>
		</div>
	</div>
{/if}

<ChatScreenDialogsAndAlerts
	{showDeleteDialog}
	{handleDeleteConfirm}
	{showEmptyFileDialog}
	{emptyFileNames}
	{activeErrorDialog}
	{handleErrorDialogOpenChange}
	{fileUpload}
/>

<style>
	@media (max-width: 768px) {
		:global(.mobile-chat-fade-mask),
		:global(.mobile-panel-fade-mask) {
			mask-image: linear-gradient(to bottom, transparent 0%, black 48px, black 100%);
			-webkit-mask-image: linear-gradient(to bottom, transparent 0%, black 48px, black 100%);
		}
	}
</style>
