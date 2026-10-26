<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Menu, PanelLeftClose, PanelLeftOpen, X } from '@lucide/svelte';
	import {
		ActionIcon,
		Logo,
		SidebarNavigationConversationList,
		SidebarNavigationActions
	} from '$lib/components/app';
	import SidebarNavigationRagPanel from './SidebarNavigationRagPanel.svelte';
	import SidebarNavigationDebugPanel from './SidebarNavigationDebugPanel.svelte';
	import SidebarNavigationTtsPanel from './SidebarNavigationTtsPanel.svelte';
	import { ROUTES } from '$lib/constants';
	import { fade } from 'svelte/transition';
	import { onMount } from 'svelte';

	import { useKeyboardShortcuts } from '$lib/hooks/use-keyboard-shortcuts.svelte';
	import { conversationsStore, conversations } from '$lib/stores/conversations.svelte';
	import { chatStore } from '$lib/stores/chat.svelte';
	import { config } from '$lib/stores/settings.svelte';
	import { RouterService } from '$lib/services/router.service';
	import { isMobile } from '$lib/stores/viewport.svelte';
	import { TooltipSide } from '$lib/enums';
	import { device } from '$lib/stores/device.svelte';
	import { circIn } from 'svelte/easing';

	interface Props {
		onSearchClick?: () => void;
	}

	let { onSearchClick = () => {} }: Props = $props();

	const { handleKeydown } = useKeyboardShortcuts({ activateSearchMode: () => onSearchClick() });

	let isExpandedMode = $state(false);
	let hoveredTooltip = $state<string | null>(null);
	let logoHovered = $state(false);
	
	let isRagPanelOpen = $state(false);
	let isDebugPanelOpen = $state(false);
	let isTtsPanelOpen = $state(false);

	// モバイル環境でのアバタータッチ連動表示
	let isMobileControlsVisible = $state(false);

	onMount(() => {
		const handleControlsChange = (e: any) => {
			isMobileControlsVisible = !!e.detail?.visible;
		};
		window.addEventListener('deiningMobileControlsChange', handleControlsChange);
		return () => {
			window.removeEventListener('deiningMobileControlsChange', handleControlsChange);
		};
	});

	const isStripExpanded = $derived(isExpandedMode || hoveredTooltip !== null);
	const isOnMobile = $derived(isMobile.current);
	const alwaysShowOnDesktop = $derived(config().alwaysShowSidebarOnDesktop as boolean);

	// Keep the sidebar expanded on desktop when the user pins it open
	$effect(() => {
		if (alwaysShowOnDesktop && !isOnMobile) {
			isExpandedMode = true;
		}
	});

	function toggleExpandedMode() {
		isExpandedMode = !isExpandedMode;
		if (!isExpandedMode) {
			hoveredTooltip = null;
		}
	}

	$effect(() => {
		if (!isExpandedMode) {
			isSearchModeActive = false;
			searchQuery = '';
			cancelMobileCollapse();
			isRagPanelOpen = false;
			isDebugPanelOpen = false;
			isTtsPanelOpen = false;
		}
	});

	function handleActionClick(action: string) {
		if (action === 'open_rag_panel') {
			isExpandedMode = true;
			isRagPanelOpen = true;
			isDebugPanelOpen = false;
			isTtsPanelOpen = false;
			isSearchModeActive = false;
		} else if (action === 'open_debug_panel') {
			isExpandedMode = true;
			isDebugPanelOpen = true;
			isTtsPanelOpen = false;
			isRagPanelOpen = false;
			isSearchModeActive = false;
		} else if (action === 'open_tts_panel') {
			isExpandedMode = true;
			isTtsPanelOpen = true;
			isDebugPanelOpen = false;
			isRagPanelOpen = false;
			isSearchModeActive = false;
		} else if (action === 'open_initial_setup') {
			window.dispatchEvent(new CustomEvent('openDeiningInitialSetup'));
		}
	}

	// On mobile the dedicated /search route hides the sidebar (see the aside
	// render guard below). Collapse it as we enter /search so it doesn't
	// reappear expanded when the user navigates back via the back button.
	$effect(() => {
		if (isMobile.current && page.url.hash.includes(ROUTES.SEARCH)) {
			isExpandedMode = false;
		}
	});

	let currentChatId = $derived(page.params.id);
	let isSearchModeActive = $state(false);
	let searchQuery = $state('');

	let filteredConversations = $derived.by(() => {
		if (isSearchModeActive) {
			if (searchQuery.trim().length > 0) {
				return conversations().filter((conversation: { name: string }) =>
					conversation.name.toLowerCase().includes(searchQuery.toLowerCase())
				);
			}

			return [];
		}

		return conversations();
	});

	async function selectConversation(id: string) {
		if (isMobile.current) {
			scheduleMobileCollapse();
		}
		await goto(RouterService.chat(id));
	}

	async function handleEditConversation(id: string) {
		const conversation = conversations().find((conv) => conv.id === id);
		if (!conversation) return;

		const newName = window.prompt('Rename conversation', conversation.name);
		if (newName && newName.trim()) {
			await conversationsStore.updateConversationName(id, newName.trim());
		}
	}

	async function handleDeleteConversation(id: string) {
		const conversation = conversations().find((conv) => conv.id === id);
		if (!conversation) return;

		const confirmed = window.confirm(
			`Delete "${conversation.name}"? This action cannot be undone.`
		);
		if (!confirmed) return;

		await conversationsStore.deleteConversation(id, { deleteWithForks: false });
	}

	function handleStopGeneration(id: string) {
		chatStore.stopGenerationForChat(id);
	}

	let innerWidth = $state(0);
	let pendingCollapse = $state<ReturnType<typeof setTimeout> | null>(null);

	function scheduleMobileCollapse() {
		if (pendingCollapse) {
			clearTimeout(pendingCollapse);
		}
		pendingCollapse = setTimeout(() => {
			isExpandedMode = false;
			pendingCollapse = null;
		}, 100);
	}

	function cancelMobileCollapse() {
		if (pendingCollapse) {
			clearTimeout(pendingCollapse);
			pendingCollapse = null;
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} bind:innerWidth />

{#if innerWidth > 768 || (!page.url.hash.includes(ROUTES.SETTINGS) && !page.url.hash.includes(ROUTES.MCP_SERVERS) && !page.url.hash.includes(ROUTES.SEARCH))}
	<aside
		class={[
			// Layout & positioning
			'fixed md:sticky top-2 left-2 md:left-0 md:ml-2 md:mt-2 pt-2 z-30 bg-starry-gradient border border-border shadow-lg flex flex-col justify-between',
			// モバイルでの幅: 展開時は画面幅いっぱい、折りたたみ時はロゴに合わせたコンパクトサイズ (w-13)
			isExpandedMode ? 'w-[calc(100dvw-1rem)]' : 'w-13',
			// Dimensions & overflow
			'md:h-[calc(100dvh-1.125rem)]',
			isExpandedMode
				? (device.isStandalone
					? 'h-[calc(100dvh-2rem)]'
					: device.isIOSDevice
						? 'h-[calc(100dvh-0.5rem)]'
						: 'h-[calc(100dvh-1rem)]')
				: 'h-13 overflow-hidden',
			// Shape & depth
			isExpandedMode ? 'rounded-3xl md:rounded-2xl' : 'rounded-full md:rounded-2xl',
			// モバイル表示制御: 展開中は常時表示、折りたたみ時はタッチ連動 (デスクトップは常に md:opacity-100 md:pointer-events-auto)
			isExpandedMode
				? 'opacity-100 pointer-events-auto'
				: (isMobileControlsVisible
					? 'opacity-100 pointer-events-auto'
					: 'opacity-0 pointer-events-none md:opacity-100 md:pointer-events-auto'),
			// Transition
			'transition-[opacity,width,height,border-radius] duration-200 ease-out md:transition-[width,padding]',
			// Expanded state: width
			isStripExpanded && 'md:w-72',
			// Collapsed state
			!isStripExpanded && 'md:w-12',
			// Expanded mode flag (for mobile ::before overlay)
			isExpandedMode && 'is-expanded'
		]}
	>
		<div class="px-1.5 flex items-center justify-between">
			<div
				role="button"
				tabindex="0"
				class="relative"
				onmouseenter={() => (logoHovered = true)}
				onmouseleave={() => (logoHovered = false)}
			>
				<ActionIcon
					icon={!isExpandedMode && logoHovered && innerWidth > 768 ? PanelLeftOpen : Logo}
					size="lg"
					iconSize="h-5.5 w-5.5 md:h-5 md:w-5"
					class="{isExpandedMode
						? 'bg-muted! md:bg-foreground/5!'
						: 'bg-transparent!'} md:h-9 md:w-9 h-10 w-10 rounded-full md:hover:bg-foreground/10! pointer-events-auto"
					href={isExpandedMode ? ROUTES.START : undefined}
					onclick={isExpandedMode ? undefined : toggleExpandedMode}
					tooltip={isExpandedMode ? undefined : 'Open Sidebar'}
					tooltipSide={TooltipSide.RIGHT}
					ariaLabel={isExpandedMode ? 'Go to start' : 'Expand navigation'}
				/>
			</div>

			{#if isExpandedMode && (!alwaysShowOnDesktop || isOnMobile)}
				<div
					class="flex items-center transition-all duration-150 ease-out"
					in:fade={{ duration: 150, easing: circIn, delay: 50 }}
					out:fade={{ duration: 100 }}
				>
					<ActionIcon
						icon={isOnMobile ? X : PanelLeftClose}
						size="lg"
						iconSize="h-4.5 w-4.5 md:h-4 md:w-4"
						class="backdrop-blur-none md:h-9 md:w-9 h-10 w-10 rounded-full mr-1 hover:bg-accent! pointer-events-auto"
						onclick={toggleExpandedMode}
						tooltip={isExpandedMode ? 'Close Sidebar' : 'Open Menu'}
						tooltipSide={TooltipSide.LEFT}
						ariaLabel={isExpandedMode ? 'Collapse navigation' : 'Open navigation menu'}
					/>
				</div>
			{/if}
		</div>

		{#if isExpandedMode || !isOnMobile}
			<div class="mt-2 flex min-h-0 flex-1 flex-col gap-4 md:gap-1 overflow-y-auto">
				<div
					class="flex min-h-0 flex-1 flex-col gap-4 md:gap-1 {isMobile.current
						? 'transition-[opacity,height] duration-200 ease-out'
						: ''}"
					in:fade={{ duration: 200 }}
					out:fade={{ duration: 200 }}
				>
					<SidebarNavigationActions
						isExpandedMode={innerWidth > 768 ? isExpandedMode : true}
						class="px-2"
						bind:isSearchModeActive
						bind:searchQuery
						onSearchDeactivated={() => {
							isSearchModeActive = false;
							searchQuery = '';
						}}
						onSearchClick={() => {
							isExpandedMode = true;
							isSearchModeActive = true;
						}}
						onNewChat={() => {
							if (isMobile.current) {
								scheduleMobileCollapse();
							}
						}}
						onActionClick={handleActionClick}
					/>

					{#if isExpandedMode}
						{#if isRagPanelOpen}
							<SidebarNavigationRagPanel onClose={() => isRagPanelOpen = false} />
						{:else if isTtsPanelOpen}
							<SidebarNavigationTtsPanel onClose={() => isTtsPanelOpen = false} />
						{:else if isDebugPanelOpen}
							<SidebarNavigationDebugPanel onClose={() => isDebugPanelOpen = false} />
						{:else}
							<SidebarNavigationConversationList
								class="px-2"
								{filteredConversations}
								{currentChatId}
								{isSearchModeActive}
								{searchQuery}
								onSelect={selectConversation}
								onEdit={handleEditConversation}
								onDelete={handleDeleteConversation}
								onStop={handleStopGeneration}
							/>
						{/if}
					{/if}
				</div>
			</div>
		{/if}
	</aside>
{/if}

<style>
	aside {
		@media (max-width: 768px) {
			--size: 1.125rem;
		}
	}

	@media (max-width: 768px) {
		aside {
			&:not(.is-expanded) {
				pointer-events: none;
			}
		}

		aside::after {
			content: '';
			position: fixed;
			top: 0;
			left: 0;
			right: 0;
			height: 0.5rem;
			background: var(--background);
			z-index: 25;
			pointer-events: none;
		}

		aside.is-expanded::before {
			content: '';
			position: fixed;
			top: -0.5rem;
			bottom: -0.25rem;
			left: -0.5rem;
			right: -0.5rem;
			z-index: -1;
			background: var(--background);
			backdrop-filter: blur(1rem);
			pointer-events: none;
		}
	}
</style>
