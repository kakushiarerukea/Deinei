<script lang="ts">
	import '../app.css';
	import { base } from '$app/paths';
	import { browser } from '$app/environment';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { untrack } from 'svelte';
	import { onMount } from 'svelte';

	import {
		SidebarNavigation,
		DialogConversationTitleUpdate,
		Live2DControls,
		DialogInitialSetup
	} from '$lib/components/app';
	import { DialogMcpServerRecommendations } from '$lib/components/app/dialogs';
	import { PwaMetaTags, PwaRefreshAlert } from '$lib/components/pwa';
	import { pwaAssetsHead } from 'virtual:pwa-assets/head';

	import { chatStore } from '$lib/stores/chat.svelte';
	import { conversationsStore } from '$lib/stores/conversations.svelte';
	import * as Tooltip from '$lib/components/ui/tooltip';
	import { isRouterMode, serverStore } from '$lib/stores/server.svelte';
	import { config, settingsStore } from '$lib/stores/settings.svelte';
	import { ModeWatcher } from 'mode-watcher';
	import { ROUTES } from '$lib/constants/routes';
	import { RouterService } from '$lib/services/router.service';
	import { Toaster } from 'svelte-sonner';
	import { modelsStore } from '$lib/stores/models.svelte';
	import { mcpStore } from '$lib/stores/mcp.svelte';
	import { TOOLTIP_DELAY_DURATION } from '$lib/constants';
	import { FAVICON_PATHS, FAVICON_SELECTORS } from '$lib/constants/pwa';
	import { useKeyboardShortcuts } from '$lib/hooks/use-keyboard-shortcuts.svelte';
	import { usePwa } from '$lib/hooks/use-pwa.svelte';
	import { useMcpRecommendations } from '$lib/hooks/use-mcp-recommendations.svelte';
	import { conversations } from '$lib/stores/conversations.svelte';
	import { isMobile } from '$lib/stores/viewport.svelte';
	import { theme } from '$lib/stores/theme.svelte';
	import { buildInfoStore } from '$lib/stores/build-info.svelte';

	import { SETTINGS_KEYS } from '$lib/constants';

	let { children } = $props();
	let innerHeight = $state<number | undefined>();
	let innerWidth = $state(browser ? window.innerWidth : 0);

	const mcpRecommendations = useMcpRecommendations();

	let chatSidebar:
		| {
				activateSearchMode?: () => void;
				editActiveConversation?: () => void;
		  }
		| undefined = $state();

	let showBuildVersion = $derived(config()[SETTINGS_KEYS.SHOW_BUILD_VERSION] as boolean);

	let titleUpdateDialogOpen = $state(false);
	let titleUpdateCurrentTitle = $state('');
	let titleUpdateNewTitle = $state('');
	let titleUpdateResolve: ((value: boolean) => void) | null = null;

	let initialSetupOpen = $state(false);

	onMount(() => {
		if (browser) {
			const setupDone = localStorage.getItem('deining_initial_setup_completed');
			if (!setupDone) {
				// 初回起動時: DOM安定後に初期設定ダイアログを表示
				setTimeout(() => {
					initialSetupOpen = true;
				}, 300);
			}

			const handleOpenSetup = () => {
				initialSetupOpen = true;
			};
			window.addEventListener('openDeiningInitialSetup', handleOpenSetup);
			return () => {
				window.removeEventListener('openDeiningInitialSetup', handleOpenSetup);
			};
		}
	});

	// Keep the hook object intact: destructuring needRefreshByStorage reads the getter once and freezes it
	const pwa = usePwa();
	const { needRefresh, updateServiceWorker } = pwa;

	function updateFavicon() {
		const isDark = typeof document !== 'undefined' && (document.documentElement.classList.contains('dark') || theme.isSystemDark);

		const link32 = document.querySelector('link[rel="icon"][sizes="32x32"]') as HTMLLinkElement | null;
		if (link32) {
			link32.href = isDark ? 'favicon-dark-32x32.png' : 'favicon-32x32.png';
		}

		const link16 = document.querySelector('link[rel="icon"][sizes="16x16"]') as HTMLLinkElement | null;
		if (link16) {
			link16.href = isDark ? 'favicon-dark-16x16.png' : 'favicon-16x16.png';
		}

		const link48 = document.querySelector('link[rel="icon"][sizes="48x48"]') as HTMLLinkElement | null;
		if (link48) {
			link48.href = isDark ? 'favicon-dark.png' : 'favicon.png';
		}

		const linkIco = document.querySelector('link[rel="shortcut icon"]') as HTMLLinkElement | null;
		if (linkIco) {
			linkIco.href = isDark ? 'favicon-dark.ico' : 'favicon.ico';
		}
	}

	function navigateToConversation(direction: -1 | 1) {
		const allConvs = conversations();

		if (allConvs.length === 0) return;

		const currentId = page.params.id;

		if (!currentId) {
			goto(RouterService.chat(allConvs[direction === 1 ? 0 : allConvs.length - 1].id));

			return;
		}

		const idx = allConvs.findIndex((c) => c.id === currentId);

		if (idx === -1) return;

		const targetIdx = idx + direction;

		if (targetIdx >= 0 && targetIdx < allConvs.length) {
			goto(RouterService.chat(allConvs[targetIdx].id));
		} else {
			goto(ROUTES.NEW_CHAT);
		}
	}

	// Global keyboard shortcuts
	const { handleKeydown } = useKeyboardShortcuts({
		editActiveConversation: () => chatSidebar?.editActiveConversation?.(),
		navigateToPrevConversation: () => navigateToConversation(-1),
		navigateToNextConversation: () => navigateToConversation(1)
	});

	function checkApiKey() {
		const apiKey = config().apiKey;

		// No API key configured — server doesn't require auth, no need to validate.
		// This mirrors the early return in validateApiKey() to avoid redundant /props requests.
		if (!apiKey || apiKey.trim() === '') {
			return;
		}

		untrack(() => {
			if (
				(page.route.id === '/(chat)' || page.route.id === '/(chat)/chat/[id]') &&
				page.status !== 401 &&
				page.status !== 403
			) {
				const headers: Record<string, string> = {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${apiKey.trim()}`
				};

				fetch(`${base}/props`, { headers })
					.then((response) => {
						if (response.status === 401 || response.status === 403) {
							window.location.reload();
						}
					})
					.catch((e) => {
						console.error('Error checking API key:', e);
					});
			}
		});
	}

	function handleTitleUpdateCancel() {
		titleUpdateDialogOpen = false;

		if (titleUpdateResolve) {
			titleUpdateResolve(false);
			titleUpdateResolve = null;
		}
	}

	function handleTitleUpdateConfirm() {
		titleUpdateDialogOpen = false;

		if (titleUpdateResolve) {
			titleUpdateResolve(true);
			titleUpdateResolve = null;
		}
	}

	onMount(() => {
		updateFavicon();
		// snapshot of every backend running stream on first load, populates the sidebar spinners
		// so the user sees each conv that has a live inference, even ones not opened yet
		void chatStore.syncRemoteRunningStreams();
	});

	// refresh that snapshot when the tab returns to the foreground, a stream may have advanced
	// or ended while it was hidden. snapshot only, no polling
	function handleVisibilityChange() {
		if (document.visibilityState !== 'visible') return;
		void chatStore.syncRemoteRunningStreams();
	}

	$effect(() => {
		void theme.isSystemDark;

		updateFavicon();
	});

	// Initialize server properties on app load (run once)
	$effect(() => {
		// Only fetch if we don't already have props
		if (!serverStore.props) {
			untrack(() => {
				serverStore.fetch();
			});
		}
	});

	// Sync settings when server props are loaded
	$effect(() => {
		const serverProps = serverStore.props;

		if (serverProps) {
			untrack(() => {
				settingsStore.syncWithServerDefaults();
			});
		}
	});

	// Inject custom CSS at runtime through an action on the head style node
	// textContent keeps the value as text, never parsed as HTML
	function customCss(node: HTMLStyleElement) {
		$effect(() => {
			node.textContent = (config().customCss as string | undefined) ?? '';
		});
	}

	// Fetch router models when in router mode (for status and modalities)
	// Wait for models to be loaded first, run only once
	let routerModelsFetched = false;

	$effect(() => {
		const isRouter = isRouterMode();
		const modelsCount = modelsStore.models.length;

		// Only fetch router models once when we have models loaded and in router mode
		if (isRouter && modelsCount > 0 && !routerModelsFetched) {
			routerModelsFetched = true;

			untrack(() => {
				modelsStore.fetchRouterModels();
			});
		}
	});

	// Live model status and load progress via the /models/sse feed (router mode)
	$effect(() => {
		if (!browser) return;
		if (!isRouterMode()) return;

		untrack(() => {
			modelsStore.subscribeStatus();
		});

		return () => {
			modelsStore.unsubscribeStatus();
		};
	});

	// Background MCP server health checks on app load
	// Fetch enabled servers from settings and run health checks in background
	$effect(() => {
		if (!browser) return;

		const mcpServers = mcpStore.getServers();

		// Only run health checks if we have enabled servers with URLs
		const enabledServers = mcpServers.filter((s) => s.enabled && s.url.trim());

		if (enabledServers.length > 0) {
			untrack(() => {
				// Run health checks in background (don't await)
				mcpStore.runHealthChecksForServers(enabledServers, false).catch((error) => {
					console.warn('[layout] MCP health checks failed:', error);
				});
			});
		}
	});

	// Monitor API key changes and redirect to error page if removed or changed when required
	$effect(() => {
		checkApiKey();
	});

	// Set up title update confirmation callback
	$effect(() => {
		conversationsStore.setTitleUpdateConfirmationCallback(
			async (currentTitle: string, newTitle: string) => {
				return new Promise<boolean>((resolve) => {
					titleUpdateCurrentTitle = currentTitle;
					titleUpdateNewTitle = newTitle;
					titleUpdateResolve = resolve;
					titleUpdateDialogOpen = true;
				});
			}
		);
	});
</script>

<svelte:head>
	{#if pwaAssetsHead.themeColor}
		<meta name="theme-color" content={pwaAssetsHead.themeColor.content} />
	{/if}

	{#if config().customCss}
		<style use:customCss></style>
	{/if}

	{#each pwaAssetsHead.links as link (link.href)}
		<link {...link} />
	{/each}

	<PwaMetaTags />
</svelte:head>

<svelte:window onkeydown={handleKeydown} bind:innerHeight bind:innerWidth />
<svelte:document onvisibilitychange={handleVisibilityChange} />

<Tooltip.Provider delayDuration={TOOLTIP_DELAY_DURATION}>
	<div class="flex flex-col md:flex-row">
		<!-- 泥濘: サイドバーとアバターの共通背景ラッパー (デスクトップで一体化) -->
		<!-- アバターが左右のUIに被るよう、全体のはみ出し描画を可能にしています -->
		<div class="flex flex-col md:flex-row bg-transparent md:bg-starry-gradient md:border-r md:border-border md:h-screen md:sticky md:top-0 shrink-0 overflow-visible">
			<SidebarNavigation
				onSearchClick={() => {
					if (isMobile.current) {
						goto(ROUTES.SEARCH);
					} else if (chatSidebar?.activateSearchMode) {
						chatSidebar.activateSearchMode();
					}
				}}
			/>

			<!-- 泥濘: Live2Dアバター (左ペイン) -->
			<!-- 親ラッパーの背景を使用するため透明化し、z-[15] を付与してサイドバー（z-10）より上に配置することで髪が被るようにしました -->
			<div id="avatar-container" class="hidden md:flex flex-col relative bg-transparent sticky top-0 h-screen overflow-visible z-[15]" style="width: 35vw; min-width: 300px; max-width: 450px;">
				
				<!-- キャンバスを大きく描画するための絶対位置の領域（左右に150px拡張、オフセット-150px） -->
				<!-- pointer-events-none を付与し、サイドバーの操作を邪魔しないようにしています -->
				<div class="absolute left-[-150px] w-[calc(100%+300px)] h-full overflow-visible pointer-events-none z-20">
					<canvas id="live2d-canvas" class="w-full h-full object-contain pointer-events-none"></canvas>
					<div id="touchZones-container" class="absolute inset-0 pointer-events-none"></div>
				</div>
				
				<Live2DControls />
			</div>
		</div>

		<div class="flex-1">
			{@render children?.()}
		</div>
	</div>

	<ModeWatcher />

	<Toaster richColors />

	<DialogConversationTitleUpdate
		bind:open={titleUpdateDialogOpen}
		currentTitle={titleUpdateCurrentTitle}
		newTitle={titleUpdateNewTitle}
		onConfirm={handleTitleUpdateConfirm}
		onCancel={handleTitleUpdateCancel}
	/>

	<DialogMcpServerRecommendations
		open={mcpRecommendations.open}
		onOpenChange={mcpRecommendations.handleOpenChange}
	/>

	<DialogInitialSetup bind:open={initialSetupOpen} />
</Tooltip.Provider>

<!-- PWA update prompt + version -->
<div class="fixed right-4 bottom-4 z-9999 flex flex-col items-end gap-1">
	{#if showBuildVersion && buildInfoStore.value}
		<span class="text-[10px] tabular-nums text-muted-foreground">{buildInfoStore.value}</span>
	{/if}
</div>
