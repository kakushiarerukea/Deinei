<script lang="ts">
	import { onMount } from 'svelte';
	import { Globe } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Tooltip from '$lib/components/ui/tooltip';
	import { toast } from 'svelte-sonner';
	import { t } from '$lib/stores/i18n.svelte';

	let searchEnabled = $state(true);
	let isToggling = $state(false);

	onMount(() => {
		const saved = localStorage.getItem('deining_web_search_enabled');
		if (saved !== null) {
			searchEnabled = saved === 'true';
			// バックエンドにも localStorage のユーザー設定を同期
			fetch('http://localhost:5000/api/search/toggle', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ enabled: searchEnabled })
			}).catch(() => {});
		} else {
			fetch('http://localhost:5000/api/search/status')
				.then((res) => (res.ok ? res.json() : null))
				.then((data) => {
					if (data && typeof data.search_enabled === 'boolean') {
						searchEnabled = data.search_enabled;
						localStorage.setItem('deining_web_search_enabled', searchEnabled ? 'true' : 'false');
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
			const res = await fetch('http://localhost:5000/api/search/toggle', { method: 'POST' });
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
				class="h-8 px-2 flex items-center gap-1.5 rounded-full transition-all text-xs font-medium {searchEnabled
					? 'bg-sky-500/15 text-sky-400 hover:bg-sky-500/25 border border-sky-500/30 shadow-xs'
					: 'text-muted-foreground/60 hover:text-muted-foreground hover:bg-muted/30 border border-transparent'}"
			>
				<Globe class="w-3.5 h-3.5 transition-transform {searchEnabled ? 'text-sky-400 scale-105' : 'opacity-50'}" />
				<span class="text-[11px] font-semibold">{searchEnabled ? 'Search' : 'Search Off'}</span>
			</Button>
		</Tooltip.Trigger>
		<Tooltip.Content side="top" class="text-xs">
			<p class="font-medium">{searchEnabled ? t('search.enabled', 'Web Search: ON') : t('search.disabled', 'Web Search: OFF (KV Cache Preserved)')}</p>
			<p class="text-[10px] text-muted-foreground mt-0.5">{t('search.toggleTooltip', 'Toggle Web Search & Weather API')}</p>
		</Tooltip.Content>
	</Tooltip.Root>
</Tooltip.Provider>
