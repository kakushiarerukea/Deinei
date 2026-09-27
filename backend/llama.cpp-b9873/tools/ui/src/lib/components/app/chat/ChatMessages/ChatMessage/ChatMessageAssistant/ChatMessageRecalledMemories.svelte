<script lang="ts">
	import { slide } from 'svelte/transition';
	import { t } from '$lib/stores/i18n.svelte';

	interface RecalledMemoriesData {
		facts?: string[];
		episodes?: string[];
		web?: string[];
	}

	interface Props {
		memories: RecalledMemoriesData;
	}

	let { memories }: Props = $props();

	let isOpen = $state(false);

	const facts = $derived(memories.facts ?? []);
	const episodes = $derived(memories.episodes ?? []);
	const web = $derived(memories.web ?? []);

	const totalCount = $derived(facts.length + episodes.length + web.length);

	let copiedIndex = $state<string | null>(null);

	function copyText(text: string, id: string) {
		navigator.clipboard.writeText(text);
		copiedIndex = id;
		setTimeout(() => {
			if (copiedIndex === id) copiedIndex = null;
		}, 2000);
	}
</script>

{#if totalCount > 0}
	<div class="recalled-memories-wrapper mt-2.5 mb-1.5 text-xs select-none">
		<!-- アコーディオン トグルボタン -->
		<button
			type="button"
			onclick={() => (isOpen = !isOpen)}
			class="recalled-trigger-btn inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-muted-foreground hover:text-foreground transition-all duration-200 border border-border/40 hover:border-border/80 bg-background/50 hover:bg-muted/40"
			aria-expanded={isOpen}
		>
			<span class="text-sm leading-none">🧠</span>
			<span class="font-medium text-[11px]">{t('chat.recalledMemories', 'Recalled Memories')}</span>
			<span class="px-1.5 py-0.2 rounded-full text-[10px] font-semibold bg-primary/15 text-primary">
				{totalCount} {t('chat.items', 'items')}
			</span>

			<!-- 内訳ミニバッジ -->
			<div class="hidden sm:inline-flex items-center gap-1 ml-1 opacity-75 text-[10px]">
				{#if facts.length > 0}
					<span class="text-emerald-400">Fact: {facts.length}</span>
				{/if}
				{#if episodes.length > 0}
					<span class="text-indigo-400">{t('chat.episodes', 'Episodes')}: {episodes.length}</span>
				{/if}
				{#if web.length > 0}
					<span class="text-sky-400">Web: {web.length}</span>
				{/if}
			</div>

			<svg
				class="w-3.5 h-3.5 ml-1 transition-transform duration-200 opacity-60"
				class:rotate-180={isOpen}
				fill="none"
				viewBox="0 0 24 24"
				stroke="currentColor"
			>
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
			</svg>
		</button>

		<!-- 展開コンテンツ -->
		{#if isOpen}
			<div
				transition:slide={{ duration: 200 }}
				class="recalled-content-panel mt-2 p-3 rounded-lg bg-card/60 backdrop-blur-md border border-border/50 shadow-sm flex flex-col gap-3"
			>
				<!-- 1. Fact 記憶 -->
				{#if facts.length > 0}
					<div class="memory-section">
						<div class="section-header flex items-center gap-1.5 text-emerald-400 font-semibold text-[11px] mb-1.5">
							<span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
							<span>{t('chat.facts', 'Facts')} ({facts.length} {t('chat.items', 'items')})</span>
						</div>
						<div class="flex flex-col gap-1 pl-3 border-l-2 border-emerald-500/30">
							{#each facts as fact, i}
								<div class="fact-item text-[11px] text-muted-foreground leading-relaxed">
									{fact}
								</div>
							{/each}
						</div>
					</div>
				{/if}

				<!-- 2. 思い出エピソード (親子リトリーバル) -->
				{#if episodes.length > 0}
					<div class="memory-section">
						<div class="section-header flex items-center gap-1.5 text-indigo-400 font-semibold text-[11px] mb-1.5">
							<span class="w-1.5 h-1.5 rounded-full bg-indigo-400"></span>
							<span>{t('chat.episodes', 'Episodes')} ({episodes.length} {t('chat.items', 'items')})</span>
						</div>
						<div class="flex flex-col gap-2">
							{#each episodes as ep, i}
								<div class="episode-card relative p-2.5 rounded-md bg-muted/40 border border-border/30 hover:border-indigo-500/30 transition-colors">
									<div class="flex items-center justify-between mb-1">
										<span class="text-[10px] font-medium text-indigo-300">
											📖 {t('chat.episodes', 'Episode')} #{i + 1}
										</span>
										<button
											type="button"
											onclick={() => copyText(ep, `ep-${i}`)}
											class="text-[10px] text-muted-foreground hover:text-foreground px-1.5 py-0.5 rounded bg-background/50 border border-border/40"
										>
											{copiedIndex === `ep-${i}` ? t('chat.copied', 'Copied!') : t('chat.copy', 'Copy')}
										</button>
									</div>
									<div class="text-[11px] text-foreground/90 leading-relaxed whitespace-pre-wrap">
										{ep}
									</div>
								</div>
							{/each}
						</div>
					</div>
				{/if}

				<!-- 3. Web 検索・気象データ -->
				{#if web.length > 0}
					<div class="memory-section">
						<div class="section-header flex items-center gap-1.5 text-sky-400 font-semibold text-[11px] mb-1.5">
							<span class="w-1.5 h-1.5 rounded-full bg-sky-400"></span>
							<span>{t('chat.web', 'Web Search Results')} ({web.length} {t('chat.items', 'items')})</span>
						</div>
						<div class="flex flex-col gap-1.5">
							{#each web as w, i}
								<div class="web-card p-2 rounded-md bg-muted/40 border border-border/30 text-[11px] text-muted-foreground leading-relaxed whitespace-pre-wrap">
									{w}
								</div>
							{/each}
						</div>
					</div>
				{/if}
			</div>
		{/if}
	</div>
{/if}

<style>
	.recalled-memories-wrapper {
		width: 100%;
	}
	.recalled-trigger-btn {
		cursor: pointer;
	}
</style>
