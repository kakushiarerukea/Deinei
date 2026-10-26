<script lang="ts">
	import { onMount } from 'svelte';
	import { Globe } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Tooltip from '$lib/components/ui/tooltip';
	import { toast } from 'svelte-sonner';
	import { t } from '$lib/stores/i18n.svelte';
	import { getGatewayBaseUrl } from '$lib/constants/api-endpoints';

	let searchEnabled = $state(false);
	let isToggling = $state(false);

	onMount(() => {
		const saved = localStorage.getItem('deining_web_search_enabled');
		if (saved !== null) {
			searchEnabled = saved === 'true';
			// バックエンドにも localStorage のユーザー設定を同期
			fetch(`${getGatewayBaseUrl()}/api/search/toggle`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ enabled: searchEnabled })
			}).catch(() => {});
		} else {
			fetch(`${getGatewayBaseUrl()}/api/search/status`)
				.then((res) => (res.ok ? res.json() : null))
				.then((data) => {
					if (data && typeof data.search_enabled === 'boolean') {
						const currentSaved = localStorage.getItem('deining_web_search_enabled');
						if (currentSaved === null) {
							searchEnabled = data.search_enabled;
							localStorage.setItem('deining_web_search_enabled', searchEnabled ? 'true' : 'false');
						} else {
							searchEnabled = currentSaved === 'true';
						}
					}
				})
				.catch(() => {});
		}

		function handleExternalChange(e: Event) {
			const customEvent = e as CustomEvent<{ enabled: boolean }>;
			if (customEvent.detail && typeof customEvent.detail.enabled === 'boolean') {
				searchEnabled = customEvent.detail.enabled;
			}
		}

		window.addEventListener('deiningWebSearchChanged', handleExternalChange);
		return () => {
			window.removeEventListener('deiningWebSearchChanged', handleExternalChange);
		};
	});

	async function handleToggle() {
		if (isToggling) return;
		isToggling = true;
		try {
			const res = await fetch(`${getGatewayBaseUrl()}/api/search/toggle`, { method: 'POST' });
			if (res.ok) {
				const data = await res.json();
				searchEnabled = data.search_enabled;
			} else {
				searchEnabled = !searchEnabled;
			}
		} catch {
			searchEnabled = !searchEnabled;
		} finally {
			localStorage.setItem('deining_web_search_enabled', searchEnabled ? 'true' : 'false');
			toast.info(searchEnabled ? t('search.enabled', 'Web Search: ON') : t('search.disabled', 'Web Search: OFF (KV Cache Preserved)'));
			isToggling = false;
		}
	}
</script>

<Tooltip.Provider>
	<Tooltip.Root delayDuration={200}>
		<Tooltip.Trigger>
			<Button
				type="button"
				variant="ghost"
				size="sm"
				onclick={handleToggle}
				disabled={isToggling}
				class="h-8 w-8 px-0 justify-center md:w-auto md:px-2 md:justify-start flex items-center md:gap-1.5 rounded-full transition-all text-xs font-medium shrink-0 {searchEnabled
					? 'bg-sky-500/15 text-sky-400 hover:bg-sky-500/25 border border-sky-500/30 shadow-xs'
					: 'text-muted-foreground/60 hover:text-muted-foreground hover:bg-muted/30 border border-transparent'}"
			>
				<Globe class="w-3.5 h-3.5 transition-transform {searchEnabled ? 'text-sky-400 scale-105' : 'opacity-50'}" />
				<span class="hidden md:inline text-[11px] font-semibold">{searchEnabled ? 'Search' : 'Search Off'}</span>
			</Button>
		</Tooltip.Trigger>
		<Tooltip.Content side="top" class="text-xs">
			<p class="font-medium">{searchEnabled ? t('search.enabled', 'Web Search: ON') : t('search.disabled', 'Web Search: OFF (KV Cache Preserved)')}</p>
			<p class="text-[10px] text-muted-foreground mt-0.5">{t('search.toggleTooltip', 'Toggle Web Search & Weather API')}</p>
		</Tooltip.Content>
	</Tooltip.Root>
</Tooltip.Provider>
