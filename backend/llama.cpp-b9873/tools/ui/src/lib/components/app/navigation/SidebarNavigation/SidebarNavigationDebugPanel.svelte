<script lang="ts">
	import { Terminal, Database, RefreshCw, Sparkles, Sliders, RotateCcw, ChevronDown, ChevronUp, Type, Archive, FileText } from '@lucide/svelte';
	import { Switch } from '$lib/components/ui/switch';
	import { conversationsStore } from '$lib/stores/conversations.svelte';
	import {
		chatStore,
		MAX_SUMMARY_BLOCKS,
		DEFAULT_SUMMARY_SYSTEM_PROMPT,
		DEFAULT_FACT_SYSTEM_PROMPT,
		getSummaryConfig
	} from '$lib/stores/chat.svelte';
	import { config, settingsStore } from '$lib/stores/settings.svelte';
	import { SETTINGS_KEYS } from '$lib/constants/settings-keys';
	import { onMount } from 'svelte';
	import { t } from '$lib/stores/i18n.svelte';
	import { estimateTokens } from '$lib/utils/context-breakdown';

	interface Props {
		class?: string;
		onClose?: () => void;
	}

	let { class: className = '', onClose }: Props = $props();

	// 未整形の平文表示 (Raw Output) 設定

	const isRawTextMode = $derived(Boolean(config().showRawPlainText));



	// タイプライター順次表示アニメーション設定
	let isTypewriterEnabled = $state(true);
	let typewriterSpeed = $state(10);

	// 現在のアクティブな会話のメッセージ・要約情報を取得
	const activeConv = $derived(conversationsStore.activeConversation);
	const activeMessages = $derived(conversationsStore.activeMessages);
	const dialogueMessages = $derived(
		activeMessages.filter(
			(m) => m.type !== 'root' && m.role !== 'system'
		)
	);
	const totalMessagesCount = $derived(dialogueMessages.length);
	const summaryBlocks = $derived(activeConv?.aiHistoryTextList ?? []);
	const archivedSummaries = $derived(activeConv?.archivedSummaries ?? []);
	const lastSummarizedIndex = $derived(activeConv?.lastSummarizedIndex ?? 0);
	const unsummarizedCount = $derived(Math.max(0, totalMessagesCount - lastSummarizedIndex));
	const remainingForSummary = $derived(Math.max(0, 30 - unsummarizedCount));
	const unsummarizedTokens = $derived.by(() => {
		const unsummarizedMsgs = dialogueMessages.slice(lastSummarizedIndex);
		let tokens = 0;
		for (const m of unsummarizedMsgs) {
			if (m.content) tokens += estimateTokens(m.content);
		}
		return tokens;
	});
	const isSlidingActive = $derived(activeConv?.isSlidingActive ?? false);
	const autoSummarize = $derived(config().autoSummarize ?? true);

	// 直近想起された記憶
	const lastRecalledMemories = $derived.by(() => {
		const msgs = [...activeMessages].reverse();
		const found = msgs.find((m) => m.recalledMemories && (
			(m.recalledMemories.facts && m.recalledMemories.facts.length > 0) ||
			(m.recalledMemories.episodes && m.recalledMemories.episodes.length > 0) ||
			(m.recalledMemories.web && m.recalledMemories.web.length > 0)
		));
		return found?.recalledMemories;
	});

	function formatTimestamp(ts?: number) {
		if (!ts) return '';
		try {
			const d = new Date(ts);
			const y = d.getFullYear();
			const m = String(d.getMonth() + 1).padStart(2, '0');
			const day = String(d.getDate()).padStart(2, '0');
			const hh = String(d.getHours()).padStart(2, '0');
			const mm = String(d.getMinutes()).padStart(2, '0');
			return `${y}/${m}/${day} ${hh}:${mm}`;
		} catch {
			return '';
		}
	}

	// プロンプト & パラメータ設定ステート
	let showPromptSettings = $state(false);

	let summaryPrompt = $state(DEFAULT_SUMMARY_SYSTEM_PROMPT);
	let summaryTemp = $state(0.4);
	let summaryMaxTokens = $state(4096);

	let factPrompt = $state(DEFAULT_FACT_SYSTEM_PROMPT);
	let factTemp = $state(0.2);
	let factMaxTokens = $state(4096);

	onMount(() => {
		



		const savedTypewriter = localStorage.getItem('deining_typewriter_enabled');
		if (savedTypewriter !== null) {
			isTypewriterEnabled = savedTypewriter === 'true';
		}
		const savedSpeed = localStorage.getItem('deining_typewriter_speed');
		if (savedSpeed !== null) {
			typewriterSpeed = parseInt(savedSpeed, 10);
		}

		const cfg = getSummaryConfig();
		summaryPrompt = cfg.summaryPrompt;
		summaryTemp = cfg.summaryTemp;
		summaryMaxTokens = cfg.summaryMaxTokens;
		factPrompt = cfg.factPrompt;
		factTemp = cfg.factTemp;
		factMaxTokens = cfg.factMaxTokens;
	});

	$effect(() => {
		if (typeof localStorage !== 'undefined') {
			localStorage.setItem('deining_typewriter_enabled', isTypewriterEnabled ? 'true' : 'false');
			localStorage.setItem('deining_typewriter_speed', typewriterSpeed.toString());
		}
	});

	function saveSettings() {
		if (typeof localStorage === 'undefined') return;
		localStorage.setItem('deining_summary_prompt', summaryPrompt);
		localStorage.setItem('deining_summary_temp', String(summaryTemp));
		localStorage.setItem('deining_summary_max_tokens', String(summaryMaxTokens));
		localStorage.setItem('deining_fact_prompt', factPrompt);
		localStorage.setItem('deining_fact_temp', String(factTemp));
		localStorage.setItem('deining_fact_max_tokens', String(factMaxTokens));
	}

	function resetToDefaults() {
		summaryPrompt = DEFAULT_SUMMARY_SYSTEM_PROMPT;
		summaryTemp = 0.4;
		summaryMaxTokens = 4096;
		factPrompt = DEFAULT_FACT_SYSTEM_PROMPT;
		factTemp = 0.2;
		factMaxTokens = 4096;
		saveSettings();
	}

	function toggleAutoSummarize() {
		settingsStore.updateConfig(SETTINGS_KEYS.AUTO_SUMMARIZE, !autoSummarize);
	}

	async function handleManualSummarize() {
		saveSettings();
		await chatStore.updateAIHistory(true);
	}

	async function handleForceRebuild() {
		saveSettings();
		if (activeConv?.id) {
			await chatStore.rebuildConversationMemory(activeConv.id, true);
		}
	}
</script>

<div class="flex flex-col h-full {className}">
	<div class="px-4 py-3 flex items-center justify-between border-b border-border">
		<h3 class="font-semibold text-sm flex items-center gap-2">
			<Terminal class="w-4 h-4" /> {t('debug.title', 'Debug')}
		</h3>
		{#if onClose}
			<button class="text-muted-foreground hover:text-foreground p-1" onclick={onClose}>✕</button>
		{/if}
	</div>

	<div class="flex-1 overflow-y-auto p-4 flex flex-col gap-6">
		<!-- Summary Info -->
		<div class="flex flex-col gap-3 bg-muted/40 p-3 rounded-lg border border-border/50">
			<h4 class="text-xs font-semibold uppercase text-muted-foreground flex items-center gap-1.5">
				<Database class="w-3.5 h-3.5" /> {t('debug.summaryStatus', 'Summary Status')}
			</h4>
			<div class="grid grid-cols-2 gap-y-2 text-xs">
				<span class="text-muted-foreground">{t('debug.conversationId', 'Conversation ID:')}</span>
				<span class="font-mono text-[10px] text-right truncate" title={activeConv?.id ?? 'None'}>
					{activeConv?.id ? activeConv.id.slice(0, 8) + '...' : 'None'}
				</span>

				<span class="text-muted-foreground">{t('debug.autoSummarize', 'Auto Summarize:')}</span>
				<button
					class="font-semibold text-right text-[11px] underline hover:opacity-80 {autoSummarize ? 'text-green-400' : 'text-amber-400'}"
					onclick={toggleAutoSummarize}
				>
					{autoSummarize ? t('debug.on', 'ON (Enabled)') : t('debug.off', 'OFF (Disabled)')}
				</button>

				<span class="text-muted-foreground">{t('debug.totalMessages', 'Total Messages:')}</span>
				<span class="font-semibold text-right">{totalMessagesCount} {t('debug.turns', 'turns')}</span>

				<span class="text-muted-foreground">{t('debug.nextSummary', 'Next Summary in:')}</span>
				<span class="font-semibold text-right text-primary">
					{t('debug.inTurns', { n: remainingForSummary }, `${remainingForSummary} turns left`)}
					<span class="text-[10px] text-muted-foreground font-normal block">({unsummarizedTokens.toLocaleString()} / 7,000t)</span>
				</span>

				<span class="text-muted-foreground">{t('debug.lastSummaryCount', 'Last Summarized Count:')}</span>
				<span class="font-semibold text-right">{lastSummarizedIndex} {t('debug.turns', 'turns')}</span>

				<span class="text-muted-foreground">{t('debug.summaryBlocksCount', 'Summary Blocks:')}</span>
				<span class="font-semibold text-right">{summaryBlocks.length} / {MAX_SUMMARY_BLOCKS}</span>

				<span class="text-muted-foreground">{t('debug.slidingWindow', 'Sliding Window:')}</span>
				<span class="font-semibold text-right {isSlidingActive ? 'text-primary' : 'text-muted-foreground'}">
					{isSlidingActive ? t('debug.active', 'Active') : t('debug.inactive', 'Inactive')}
				</span>
			</div>

			<!-- Debug Action Buttons -->
			<div class="flex flex-col gap-1.5 pt-2 border-t border-border/40">
				<button
					class="w-full flex items-center justify-center gap-1.5 px-2.5 py-1.5 text-xs font-medium bg-primary/20 hover:bg-primary/30 text-primary border border-primary/30 rounded transition-colors disabled:opacity-50"
					disabled={chatStore.isSummarizing || !activeConv}
					onclick={handleManualSummarize}
				>
					<Sparkles class="w-3.5 h-3.5" /> {t('debug.forceRun', 'Force Run Summary Now')}
				</button>
				<button
					class="w-full flex items-center justify-center gap-1.5 px-2.5 py-1.5 text-xs font-medium bg-muted hover:bg-muted/80 text-muted-foreground border border-border/60 rounded transition-colors disabled:opacity-50"
					disabled={chatStore.isSummarizing || !activeConv}
					onclick={handleForceRebuild}
				>
					<RefreshCw class="w-3.5 h-3.5" /> {t('debug.rebuild', 'Rebuild Conversation Memory')}
				</button>
			</div>
		</div>

		<!-- Raw Output (Unformatted Plain Text) Display Settings -->

		<div class="flex flex-col gap-3 bg-muted/40 p-3 rounded-lg border border-border/50">

			<div class="flex items-center justify-between">

				<h4 class="text-xs font-semibold uppercase text-muted-foreground flex items-center gap-1.5">

					<FileText class="w-3.5 h-3.5" /> {t('debug.rawOutputTitle', 'Raw Output (未整形の平文)')}

				</h4>

				<Switch checked={isRawTextMode} onCheckedChange={(checked) => settingsStore.updateConfig(SETTINGS_KEYS.SHOW_RAW_PLAIN_TEXT, checked)} />

			</div>

			<div class="text-[11px] text-muted-foreground">

				{t('debug.rawOutputDesc', '絵文字の非表示やト書き装飾を行わず、LLMが出力したそのままの平文を表示します。')}

			</div>

		</div>



		<!-- Typewriter Animation Settings -->
		<div class="flex flex-col gap-3 bg-muted/40 p-3 rounded-lg border border-border/50">
			<div class="flex items-center justify-between">
				<h4 class="text-xs font-semibold uppercase text-muted-foreground flex items-center gap-1.5">
					<Type class="w-3.5 h-3.5" /> {t('debug.typewriterTitle', 'Typewriter Animation')}
				</h4>
				<Switch bind:checked={isTypewriterEnabled} />
			</div>
			<div class="text-[11px] text-muted-foreground">
				{t('debug.typewriterDesc', 'Displays pre-read paragraphs character-by-character sequentially.')}
			</div>
			{#if isTypewriterEnabled}
				<div class="flex flex-col gap-2 pt-2 border-t border-border/40 text-xs">
					<div class="flex items-center justify-between">
						<span class="text-muted-foreground">{t('debug.speed', 'Display Speed:')} <span class="font-semibold text-foreground">{typewriterSpeed} TPS</span></span>
						<button class="text-[11px] text-primary hover:underline" onclick={() => (typewriterSpeed = 10)}>{t('debug.reset', 'Reset')} (10)</button>
					</div>
					<input
						type="range"
						min="5"
						max="40"
						step="1"
						bind:value={typewriterSpeed}
						class="w-full h-1.5 rounded-lg appearance-none cursor-pointer bg-muted"
					/>
				</div>
			{/if}
		</div>

		<!-- Prompt & Parameter Tuning Accordion -->
		<div class="flex flex-col gap-2 bg-muted/30 p-3 rounded-lg border border-border/50">
			<button
				class="flex items-center justify-between w-full text-xs font-semibold uppercase text-muted-foreground hover:text-foreground transition-colors"
				onclick={() => (showPromptSettings = !showPromptSettings)}
			>
				<span class="flex items-center gap-1.5">
					<Sliders class="w-3.5 h-3.5" /> {t('debug.promptSettings', 'Summary & RAG Settings')}
				</span>
				{#if showPromptSettings}
					<ChevronUp class="w-4 h-4" />
				{:else}
					<ChevronDown class="w-4 h-4" />
				{/if}
			</button>

			{#if showPromptSettings}
				<div class="flex flex-col gap-4 pt-2 border-t border-border/40 text-xs">
					<!-- あらすじ要約設定 -->
					<div class="flex flex-col gap-2">
						<span class="font-semibold text-primary">{t('debug.summarySettings', '📝 Summary Prompt Settings')}</span>
						<label class="text-[11px] text-muted-foreground flex flex-col gap-1">
							{t('debug.systemPrompt', 'System Prompt:')}
							<textarea
								class="w-full h-20 p-2 bg-background border border-border rounded text-[11px] font-sans resize-y"
								bind:value={summaryPrompt}
								oninput={saveSettings}
							></textarea>
						</label>
						<div class="grid grid-cols-2 gap-2">
							<label class="text-[11px] text-muted-foreground flex flex-col gap-1">
								Temperature:
								<input
									type="number"
									step="0.1"
									min="0"
									max="2"
									class="px-2 py-1 bg-background border border-border rounded text-[11px]"
									bind:value={summaryTemp}
									oninput={saveSettings}
								/>
							</label>
							<label class="text-[11px] text-muted-foreground flex flex-col gap-1">
								Max Tokens:
								<input
									type="number"
									step="128"
									min="512"
									max="8192"
									class="px-2 py-1 bg-background border border-border rounded text-[11px]"
									bind:value={summaryMaxTokens}
									oninput={saveSettings}
								/>
							</label>
						</div>
					</div>

					<!-- RAG事実抽出設定 -->
					<div class="flex flex-col gap-2 border-t border-border/30 pt-2">
						<span class="font-semibold text-primary">{t('debug.factSettings', '🧠 Fact Extraction Settings')}</span>
						<label class="text-[11px] text-muted-foreground flex flex-col gap-1">
							{t('debug.systemPrompt', 'System Prompt:')}
							<textarea
								class="w-full h-20 p-2 bg-background border border-border rounded text-[11px] font-sans resize-y"
								bind:value={factPrompt}
								oninput={saveSettings}
							></textarea>
						</label>
						<div class="grid grid-cols-2 gap-2">
							<label class="text-[11px] text-muted-foreground flex flex-col gap-1">
								Temperature:
								<input
									type="number"
									step="0.1"
									min="0"
									max="2"
									class="px-2 py-1 bg-background border border-border rounded text-[11px]"
									bind:value={factTemp}
									oninput={saveSettings}
								/>
							</label>
							<label class="text-[11px] text-muted-foreground flex flex-col gap-1">
								Max Tokens:
								<input
									type="number"
									step="128"
									min="512"
									max="8192"
									class="px-2 py-1 bg-background border border-border rounded text-[11px]"
									bind:value={factMaxTokens}
									oninput={saveSettings}
								/>
							</label>
						</div>
					</div>

					<!-- Reset Button -->
					<button
						class="w-full flex items-center justify-center gap-1 px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground bg-muted/60 hover:bg-muted rounded border border-border/40 transition-colors"
						onclick={resetToDefaults}
					>
						<RotateCcw class="w-3 h-3" /> {t('debug.restoreDefaults', 'Restore Defaults')}
					</button>
				</div>
			{/if}
		</div>

		<!-- Summary Blocks Content List -->
		<div class="flex flex-col gap-2 flex-1">
			<h4 class="text-xs font-semibold uppercase text-muted-foreground">{t('debug.summaryBlocks', 'Summary Blocks Content')} ({summaryBlocks.length} / {MAX_SUMMARY_BLOCKS})</h4>

			<div class="flex-1 min-h-[220px] max-h-[350px] bg-black/40 rounded-md border border-border p-2 overflow-y-auto space-y-3">
				{#each summaryBlocks as block, i}
					<div class="bg-background/80 border border-border/60 rounded p-2.5 text-xs">
						<div class="flex items-center justify-between border-b border-border/40 pb-1 mb-1.5">
							<span class="font-semibold text-primary">Block {i + 1}</span>
							{#if isSlidingActive && i === 0}
								<span class="text-[9px] bg-primary/20 text-primary px-1.5 py-0.5 rounded">{t('debug.oldestSummary', 'Oldest Summary')}</span>
							{/if}
						</div>
						<p class="text-muted-foreground whitespace-pre-wrap leading-relaxed">{block}</p>
					</div>
				{/each}

				{#if summaryBlocks.length === 0}
					<div class="text-muted-foreground italic text-xs text-center py-8">
						{t('debug.noSummaryBlocks', 'No summary blocks yet.')}<br>
						<span class="text-[10px] opacity-70">{t('debug.noSummaryBlocksHelp', '(Conversation has fewer than 30 turns or not generated)')}</span>
					</div>
				{/if}
			</div>
		</div>

		<!-- Archived Forgotten Episodes List (Parent-Child) -->
		<div class="flex flex-col gap-2">
			<div class="flex items-center justify-between">
				<h4 class="text-xs font-semibold uppercase text-muted-foreground flex items-center gap-1.5">
					<Archive class="w-3.5 h-3.5" /> {t('debug.archivedEpisodes', 'Pushed Archived Episodes')} ({archivedSummaries.length})
				</h4>
			</div>

			<div class="bg-black/30 rounded-md border border-border/60 p-2 overflow-y-auto max-h-[280px] space-y-2">
				{#each archivedSummaries as archive, idx}
					<div class="bg-background/60 border border-primary/20 rounded p-2.5 text-xs">
						<div class="flex items-center justify-between border-b border-border/30 pb-1 mb-1.5">
							<span class="font-semibold text-primary/90">Archive #{idx + 1}</span>
							<span class="text-[9px] px-1.5 py-0.5 rounded {archive.status === 'completed' ? 'bg-primary/20 text-primary border border-primary/30' : 'bg-yellow-500/20 text-yellow-500 border border-yellow-500/30'}">
								{archive.status === 'completed' ? t('debug.archivedStatusCompleted', 'RAG Indexed') : t('debug.archivedStatusPending', 'Pending Index')}
							</span>
						</div>
						<p class="text-muted-foreground whitespace-pre-wrap leading-relaxed text-[11px]">{archive.parentText}</p>
						{#if archive.timestamp}
							<div class="text-[9px] text-muted-foreground/50 mt-1.5 select-none">
								{t('debug.archivedTime', 'Archived at:')} {formatTimestamp(archive.timestamp)}
							</div>
						{/if}
					</div>
				{/each}

				{#if archivedSummaries.length === 0}
					<div class="text-muted-foreground italic text-[11px] text-center py-4">
						{t('debug.noArchived', 'No archived episodes yet.')}<br>
						<span class="text-[9px] opacity-70">{t('debug.noArchivedHelp', '(Episodes are accumulated here when summary blocks exceed limit)')}</span>
					</div>
				{/if}
			</div>
		</div>

		<!-- Last Recalled Memories Section -->
		<div class="flex flex-col gap-2">
			<h4 class="text-xs font-semibold uppercase text-muted-foreground flex items-center gap-1.5">
				{t('debug.lastRecalledTitle', '🧠 Recently Recalled Memories (RAG / Web)')}
			</h4>

			<div class="bg-black/30 rounded-md border border-border/60 p-2.5 space-y-2">
				{#if lastRecalledMemories}
					{#if lastRecalledMemories.facts && lastRecalledMemories.facts.length > 0}
						<div>
							<span class="text-[10px] font-semibold text-emerald-400">{t('debug.recalledFacts', '🟢 Fact Memories:')}</span>
							<ul class="list-disc list-inside text-[11px] text-muted-foreground mt-0.5 space-y-0.5">
								{#each lastRecalledMemories.facts as f}
									<li>{f}</li>
								{/each}
							</ul>
						</div>
					{/if}
					{#if lastRecalledMemories.episodes && lastRecalledMemories.episodes.length > 0}
						<div>
							<span class="text-[10px] font-semibold text-indigo-400">{t('debug.recalledEpisodes', '🟣 Parent-Child Retrieval (Episodes):')}</span>
							<div class="mt-1 space-y-1">
								{#each lastRecalledMemories.episodes as ep, i}
									<div class="p-2 rounded bg-background/60 border border-border/40 text-[11px] text-foreground/90 whitespace-pre-wrap">
										{ep}
									</div>
								{/each}
							</div>
						</div>
					{/if}
					{#if lastRecalledMemories.web && lastRecalledMemories.web.length > 0}
						<div>
							<span class="text-[10px] font-semibold text-sky-400">{t('debug.recalledWeb', '🌐 Web Search Results:')}</span>
							<div class="mt-1 space-y-1">
								{#each lastRecalledMemories.web as w}
									<div class="p-1.5 rounded bg-background/60 border border-border/40 text-[10px] text-muted-foreground">
										{w}
									</div>
								{/each}
							</div>
						</div>
					{/if}
				{:else}
					<div class="text-muted-foreground italic text-[11px] text-center py-3">
						{t('debug.noRecalled', 'No recent memory retrieval logs.')}<br>
						<span class="text-[9px] opacity-70">{t('debug.noRecalledHelp', '(Triggered RAG or parent-child retrieval results will appear here)')}</span>
					</div>
				{/if}
			</div>
		</div>
	</div>
</div>
