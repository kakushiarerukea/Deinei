<script lang="ts">
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Tooltip from '$lib/components/ui/tooltip';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import {
		Sparkles,
		Volume2,
		Languages,
		Search,
		Layers,
		Bot,
		Check,
		Info
	} from '@lucide/svelte';
	import { onMount } from 'svelte';
	import { t, i18n, type SupportedLocale } from '$lib/stores/i18n.svelte';
	import { settingsStore } from '$lib/stores/settings.svelte';
	import { SETTINGS_KEYS } from '$lib/constants/settings-keys';
	import { DEFAULT_SYSTEM_PROMPT } from '$lib/constants/default-system-prompt';
	import { conversationsStore } from '$lib/stores/conversations.svelte';
	import { chatStore } from '$lib/stores/chat.svelte';
	import { getGatewayBaseUrl } from '$lib/constants/api-endpoints';

	const searchTriggerKeywordsJa = [
		'検索して', '調べて', 'ググって', '調査して', '〜を教えて', '〜について教えて', '〜について知りたい',
		'天気予報', '最新ニュース'
	];

	const searchTriggerKeywordsEn = [
		'search for', 'look up', 'google it', 'weather forecast', 'latest news', 'tell me about'
	];

	interface Props {
		open?: boolean;
		onComplete?: () => void;
	}

	let { open = $bindable(false), onComplete }: Props = $props();

	let selectedLocale = $state<SupportedLocale>('ja');
	let isTtsEnabled = $state(true);
	let isTtsSyncEnabled = $state(true);
	let isWebSearchEnabled = $state(false);
	let isSamplePromptEnabled = $state(true);
	let initialCustomPrompt = $state('');

	function loadCurrentSettings() {
		selectedLocale = i18n.currentLocale;

		const savedTts = localStorage.getItem('deining_tts_enabled');
		isTtsEnabled = savedTts !== null ? savedTts === 'true' : true;

		const savedSync = localStorage.getItem('deining_tts_sync_display');
		isTtsSyncEnabled = savedSync !== null ? savedSync === 'true' : true;

		const savedSearch = localStorage.getItem('deining_web_search_enabled');
		isWebSearchEnabled = savedSearch !== null ? savedSearch === 'true' : false;

		const currentPrompt = (settingsStore.config[SETTINGS_KEYS.SYSTEM_MESSAGE] ?? '').toString().trim();
		initialCustomPrompt = currentPrompt;
		const isInitialRun = !localStorage.getItem('deining_initial_setup_completed');
		isSamplePromptEnabled = isInitialRun || currentPrompt === DEFAULT_SYSTEM_PROMPT.trim();
	}

	$effect(() => {
		if (open) {
			loadCurrentSettings();
		}
	});

	onMount(() => {
		loadCurrentSettings();
	});

	function handleLocaleChange(locale: SupportedLocale) {
		selectedLocale = locale;
		i18n.setLocale(locale);
	}

	async function handleComplete() {
		// 1. 言語の保存
		i18n.setLocale(selectedLocale);
		localStorage.setItem('deining_language', selectedLocale);

		// 2. TTS設定の保存 & イベント通知
		localStorage.setItem('deining_tts_enabled', isTtsEnabled ? 'true' : 'false');
		localStorage.setItem('deining_tts_sync_display', isTtsSyncEnabled ? 'true' : 'false');
		if (typeof window !== 'undefined') {
			window.dispatchEvent(
				new CustomEvent('deiningTtsSettingsChanged', {
					detail: { ttsEnabled: isTtsEnabled, syncEnabled: isTtsSyncEnabled }
				})
			);
		}

		// 3. Web検索ツール設定の保存 & バックエンド同期 & イベント通知
		localStorage.setItem('deining_web_search_enabled', isWebSearchEnabled ? 'true' : 'false');
		if (typeof window !== 'undefined') {
			window.dispatchEvent(
				new CustomEvent('deiningWebSearchChanged', {
					detail: { enabled: isWebSearchEnabled }
				})
			);
		}
		try {
			await fetch(`${getGatewayBaseUrl()}/api/search/toggle`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ enabled: isWebSearchEnabled })
			}).catch(() => {});
		} catch {}

		// 4. システムプロンプトの適用 & チャットへの即時同期
		let targetPrompt = '';
		if (isSamplePromptEnabled) {
			targetPrompt = DEFAULT_SYSTEM_PROMPT;
			settingsStore.updateConfig(SETTINGS_KEYS.SYSTEM_MESSAGE, DEFAULT_SYSTEM_PROMPT);
		} else {
			// オフの場合：初期値がデフォルトプロンプトだったものをオフにした場合は空文字に設定
			if (!initialCustomPrompt || initialCustomPrompt === DEFAULT_SYSTEM_PROMPT.trim()) {
				targetPrompt = '';
				settingsStore.updateConfig(SETTINGS_KEYS.SYSTEM_MESSAGE, '');
			} else {
				// ユーザー独自のカスタムプロンプトが入っていた場合はそのまま維持
				targetPrompt = initialCustomPrompt;
				settingsStore.updateConfig(SETTINGS_KEYS.SYSTEM_MESSAGE, initialCustomPrompt);
			}
		}

		// 現在開いている会話のシステムメッセージがあれば即座に更新
		const sysMsg = conversationsStore.activeMessages.find((m) => m.role === 'system');
		if (sysMsg) {
			try {
				await chatStore.updateMessage(sysMsg.id, targetPrompt);
			} catch (e) {
				console.warn('Failed to update active conversation system prompt:', e);
			}
		}

		// 5. デフォルトでは Reasoning effort を OFF に設定
		await conversationsStore.setThinkingEnabled(false);

		// 6. 初回セットアップ完了フラグの保存
		localStorage.setItem('deining_initial_setup_completed', 'true');

		open = false;
		onComplete?.();
	}
</script>

<Dialog.Root bind:open closeOnOutsideClick={false}>
	<Dialog.Content class="max-w-2xl max-h-[92vh] overflow-y-auto p-4 sm:p-5 rounded-2xl border border-border/80 bg-background/95 backdrop-blur-xl shadow-2xl">
		<!-- Header -->
		<Dialog.Header class="flex flex-col gap-1 text-left border-b border-border/60 pb-2.5">
			<div class="flex items-center gap-2">
				<div class="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary border border-primary/20 shrink-0">
					<Sparkles class="h-4 w-4" />
				</div>
				<div>
					<Dialog.Title class="text-lg font-bold tracking-tight text-foreground flex items-center gap-2">
						{t('setup.wizard.title', '泥濘 (Deinei) へようこそ！')}
					</Dialog.Title>
					<span class="text-[11px] font-medium text-primary tracking-wider uppercase">
						{t('setup.wizard.subtitle', '初期設定ウィザード')}
					</span>
				</div>
			</div>
			<Dialog.Description class="text-xs text-muted-foreground mt-0.5">
				{t('setup.wizard.desc', 'あなたの利用スタイルに合わせて基本機能を簡単に設定できます（※後からいつでも各パネルや設定から変更可能です）。')}
			</Dialog.Description>
			{#if selectedLocale === 'en'}
				<div class="mt-1.5 rounded-lg bg-red-500/10 border border-red-500/30 px-2.5 py-1.5 text-xs text-red-600 dark:text-red-400 font-medium leading-normal flex items-center gap-2">
					<span class="shrink-0 text-xs">⚠️</span>
					<span>
						<strong>Notice:</strong> ruiri-v3 and Irodori-TTS are dedicated to Japanese. Alternative solutions for English support are currently being explored and considered.
					</span>
				</div>
			{/if}
		</Dialog.Header>

		<!-- Settings Options Form -->
		<div class="flex flex-col gap-2.5 py-3">
			<!-- Option 1: 言語選択 (Language) -->
			<div class="rounded-xl border border-border/50 bg-card/40 p-2.5 px-3.5 transition-all hover:border-primary/30">
				<div class="flex items-center justify-between gap-3">
					<div class="flex items-center gap-2.5">
						<div class="p-1 rounded-lg bg-blue-500/10 text-blue-500 shrink-0">
							<Languages class="w-4 h-4" />
						</div>
						<div>
							<span class="text-sm font-semibold text-foreground">
								{t('setup.wizard.language', '1. 表示言語 (Language)')}
							</span>
							<p class="text-[11px] text-muted-foreground">
								{t('setup.wizard.languageDesc', 'UIの表示言語を選択してください。')}
							</p>
						</div>
					</div>
					<!-- Language Toggle Buttons -->
					<div class="flex items-center gap-1 rounded-lg bg-muted/60 p-0.5 border border-border/40 shrink-0">
						<button
							type="button"
							class="px-2.5 py-1 text-xs font-medium rounded-md transition-all cursor-pointer {selectedLocale === 'ja' ? 'bg-background text-foreground shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'}"
							onclick={() => handleLocaleChange('ja')}
						>
							🇯🇵 日本語
						</button>
						<button
							type="button"
							class="px-2.5 py-1 text-xs font-medium rounded-md transition-all cursor-pointer {selectedLocale === 'en' ? 'bg-background text-foreground shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'}"
							onclick={() => handleLocaleChange('en')}
						>
							🇺🇸 English
						</button>
					</div>
				</div>
			</div>

			<!-- Option 2: TTSの有無 (Speech Synthesis) -->
			<div
				role="button"
				tabindex="0"
				class="rounded-xl border p-2.5 px-3.5 transition-all cursor-pointer select-none {isTtsEnabled ? 'border-primary/40 bg-primary/[0.03] shadow-xs' : 'border-border/50 bg-card/40 opacity-80 hover:border-border/80'}"
				onclick={() => { isTtsEnabled = !isTtsEnabled; }}
				onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); isTtsEnabled = !isTtsEnabled; } }}
			>
				<div class="flex items-center justify-between gap-3">
					<div class="flex items-center gap-2.5">
						<div class="p-1 rounded-lg bg-emerald-500/10 text-emerald-500 shrink-0">
							<Volume2 class="w-4 h-4" />
						</div>
						<div>
							<div class="flex items-center gap-2">
								<span class="text-sm font-semibold text-foreground">
									{t('setup.wizard.tts', '2. 音声合成 (Irodori-TTS)')}
								</span>
								<Badge variant="secondary" class="text-[10px] px-1.5 py-0 h-4 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20">
									Recommended
								</Badge>
							</div>
							<p class="text-[11px] text-muted-foreground mt-0.5">
								{t('setup.wizard.ttsDesc', 'アシスタントの返答をリアルタイム音声合成で自動読み上げします。')}
							</p>
						</div>
					</div>
					<!-- ON / OFF Buttons -->
					<div class="flex items-center gap-1 rounded-lg bg-muted/60 p-0.5 border border-border/40 shrink-0">
						<button
							type="button"
							class="px-2.5 py-1 text-xs font-medium rounded-md transition-all cursor-pointer {isTtsEnabled ? 'bg-emerald-600 text-white shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'}"
							onclick={(e) => { e.stopPropagation(); isTtsEnabled = true; }}
						>
							ON
						</button>
						<button
							type="button"
							class="px-2.5 py-1 text-xs font-medium rounded-md transition-all cursor-pointer {!isTtsEnabled ? 'bg-background text-foreground shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'}"
							onclick={(e) => { e.stopPropagation(); isTtsEnabled = false; }}
						>
							OFF
						</button>
					</div>
				</div>
			</div>

			<!-- Option 3: TTSと文章のシンク機能 (Sync Text with Speech) -->
			<div
				role="button"
				tabindex={isTtsEnabled ? 0 : -1}
				class="rounded-xl border p-2.5 px-3.5 transition-all select-none {isTtsEnabled ? 'cursor-pointer' : 'opacity-40 cursor-not-allowed'} {isTtsSyncEnabled && isTtsEnabled ? 'border-primary/40 bg-primary/[0.03] shadow-xs' : 'border-border/50 bg-card/40 hover:border-border/80'}"
				onclick={() => { if (isTtsEnabled) isTtsSyncEnabled = !isTtsSyncEnabled; }}
				onkeydown={(e) => { if (isTtsEnabled && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); isTtsSyncEnabled = !isTtsSyncEnabled; } }}
			>
				<div class="flex items-center justify-between gap-3">
					<div class="flex items-center gap-2.5">
						<div class="p-1 rounded-lg bg-indigo-500/10 text-indigo-500 shrink-0">
							<Layers class="w-4 h-4" />
						</div>
						<div>
							<div class="flex items-center gap-2">
								<span class="text-sm font-semibold text-foreground">
									{t('setup.wizard.ttsSync', '3. 音声と文章のシンク表示')}
								</span>
							</div>
							<p class="text-[11px] text-muted-foreground mt-0.5">
								{t('setup.wizard.ttsSyncDesc', '音声の読み上げ進行に合わせて、画面上の文章を1文ずつ字幕のように同期表示します（OFFにすると全文が一括で表示されます）。')}
							</p>
						</div>
					</div>
					<!-- ON / OFF Buttons -->
					<div class="flex items-center gap-1 rounded-lg bg-muted/60 p-0.5 border border-border/40 shrink-0">
						<button
							type="button"
							disabled={!isTtsEnabled}
							class="px-2.5 py-1 text-xs font-medium rounded-md transition-all {isTtsSyncEnabled && isTtsEnabled ? 'bg-primary text-primary-foreground shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:cursor-not-allowed'}"
							onclick={(e) => { e.stopPropagation(); if (isTtsEnabled) isTtsSyncEnabled = true; }}
						>
							ON
						</button>
						<button
							type="button"
							disabled={!isTtsEnabled}
							class="px-2.5 py-1 text-xs font-medium rounded-md transition-all {!isTtsSyncEnabled && isTtsEnabled ? 'bg-background text-foreground shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:cursor-not-allowed'}"
							onclick={(e) => { e.stopPropagation(); if (isTtsEnabled) isTtsSyncEnabled = false; }}
						>
							OFF
						</button>
					</div>
				</div>
			</div>

			<!-- Option 4: サーチツール (ddgs) の有無 (Web Search) -->
			<div
				role="button"
				tabindex="0"
				class="rounded-xl border p-2.5 px-3.5 transition-all cursor-pointer select-none {isWebSearchEnabled ? 'border-primary/40 bg-primary/[0.03] shadow-xs' : 'border-border/50 bg-card/40 opacity-80 hover:border-border/80'}"
				onclick={() => { isWebSearchEnabled = !isWebSearchEnabled; }}
				onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); isWebSearchEnabled = !isWebSearchEnabled; } }}
			>
				<div class="flex items-center justify-between gap-3 mb-1">
					<div class="flex items-center gap-2.5">
						<div class="p-1 rounded-lg bg-amber-500/10 text-amber-500 shrink-0">
							<Search class="w-4 h-4" />
						</div>
						<div class="flex items-center gap-2 flex-wrap">
							<span class="text-sm font-semibold text-foreground">
								{t('setup.wizard.webSearch', '4. リアルタイムWeb検索 (DuckDuckGo)')}
							</span>
							<Badge variant="outline" class="text-[10px] px-1.5 py-0 h-4 border-amber-500/30 text-amber-600 dark:text-amber-400">
								Keyword Trigger
							</Badge>
						</div>
					</div>
					<!-- ON / OFF Buttons -->
					<div class="flex items-center gap-1 rounded-lg bg-muted/60 p-0.5 border border-border/40 shrink-0">
						<button
							type="button"
							class="px-2.5 py-1 text-xs font-medium rounded-md transition-all cursor-pointer {isWebSearchEnabled ? 'bg-amber-600 text-white shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'}"
							onclick={(e) => { e.stopPropagation(); isWebSearchEnabled = true; }}
						>
							ON
						</button>
						<button
							type="button"
							class="px-2.5 py-1 text-xs font-medium rounded-md transition-all cursor-pointer {!isWebSearchEnabled ? 'bg-background text-foreground shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'}"
							onclick={(e) => { e.stopPropagation(); isWebSearchEnabled = false; }}
						>
							OFF
						</button>
					</div>
				</div>
				<div class="pl-7 flex flex-col gap-1">
					<p class="text-[11px] text-muted-foreground leading-snug">
						{t('setup.wizard.webSearchDesc', '最新ニュースや天気などをDuckDuckGoで検索します。※常時発火ではなく、「検索」「調べ」「最新の」「天気」「ニュース」などのキーワード発言時のみ自動で検索を実行します。')}
					</p>
					<div class="flex items-center gap-1.5 text-[11px] text-amber-600 dark:text-amber-400/90 font-medium">
						<Tooltip.Root delayDuration={50}>
							<Tooltip.Trigger
								class="inline-flex items-center gap-1 hover:underline cursor-pointer group"
								onclick={(e) => e.stopPropagation()}
							>
								<span class="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20 group-hover:bg-amber-500/20 transition-colors">
									<Info class="w-2.5 h-2.5 shrink-0" />
								</span>
								<span>{selectedLocale === 'ja' ? '発火キーワード一覧（カーソルを合わせると全表示）' : 'Trigger Keywords (Hover to view all)'}</span>
							</Tooltip.Trigger>
							<Tooltip.Content side="top" align="start" class="max-w-xs p-3.5 bg-popover/95 backdrop-blur-md border border-border shadow-xl text-foreground text-xs rounded-xl z-[9999]">
								<div class="font-semibold mb-1 text-primary flex items-center gap-1.5 text-xs">
									<Search class="w-3.5 h-3.5" />
									{selectedLocale === 'ja' ? '検知対象キーワード一覧' : 'Trigger Keywords List'}
								</div>
								<div class="text-[11px] text-muted-foreground mb-2 leading-tight">
									{selectedLocale === 'ja' ? 'ユーザーの発言に以下のいずれかが含まれると自動発火します：' : 'Search triggers when any of these keywords are present in your message:'}
								</div>
								<div class="flex flex-wrap gap-1 max-h-36 overflow-y-auto">
									{#if selectedLocale === 'ja'}
										{#each searchTriggerKeywordsJa as kw}
											<span class="px-1.5 py-0.5 rounded bg-muted text-[11px] font-mono border border-border/50 text-foreground">{kw}</span>
										{/each}
									{:else}
										{#each searchTriggerKeywordsEn as kw}
											<span class="px-1.5 py-0.5 rounded bg-muted text-[11px] font-mono border border-border/50 text-foreground">{kw}</span>
										{/each}
									{/if}
								</div>
								<div class="mt-2 pt-1.5 border-t border-border/40 text-[10px] text-muted-foreground leading-snug">
									{selectedLocale === 'ja'
										? '※日常会話の誤発火を防ぐため、「いい天気」や単なる「それ教えて」等では反応しません（「今何時」等の日時質問はプロンプトから即答されます）。'
										: '※Casual phrases like "nice weather" or "tell me more" are ignored to prevent misfires (time/date questions are answered directly).'}
								</div>
							</Tooltip.Content>
						</Tooltip.Root>
					</div>
				</div>
			</div>

			<!-- Option 5: デフォルトのサンプルシステムプロンプト (Sample System Prompt) -->
			<div
				role="button"
				tabindex="0"
				class="rounded-xl border p-2.5 px-3.5 transition-all cursor-pointer select-none {isSamplePromptEnabled ? 'border-primary/40 bg-primary/[0.03] shadow-xs' : 'border-border/50 bg-card/40 opacity-80 hover:border-border/80'}"
				onclick={() => { isSamplePromptEnabled = !isSamplePromptEnabled; }}
				onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); isSamplePromptEnabled = !isSamplePromptEnabled; } }}
			>
				<div class="flex items-center justify-between gap-3 mb-1">
					<div class="flex items-center gap-2.5">
						<div class="p-1 rounded-lg bg-purple-500/10 text-purple-500 shrink-0">
							<Bot class="w-4 h-4" />
						</div>
						<div class="flex items-center gap-2 flex-wrap">
							<span class="text-sm font-semibold text-foreground">
								{t('setup.wizard.systemPrompt', '5. サンプルシステムプロンプト')}
							</span>
							<Badge variant="secondary" class="text-[10px] px-1.5 py-0 h-4 bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20">
								{selectedLocale === 'ja' ? '一般的な恋人との対話' : 'Romantic Partner'}
							</Badge>
						</div>
					</div>
					<!-- ON / OFF Buttons -->
					<div class="flex items-center gap-1 rounded-lg bg-muted/60 p-0.5 border border-border/40 shrink-0">
						<button
							type="button"
							class="px-2.5 py-1 text-xs font-medium rounded-md transition-all cursor-pointer {isSamplePromptEnabled ? 'bg-purple-600 text-white shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'}"
							onclick={(e) => { e.stopPropagation(); isSamplePromptEnabled = true; }}
						>
							ON
						</button>
						<button
							type="button"
							class="px-2.5 py-1 text-xs font-medium rounded-md transition-all cursor-pointer {!isSamplePromptEnabled ? 'bg-background text-foreground shadow-sm font-semibold' : 'text-muted-foreground hover:text-foreground'}"
							onclick={(e) => { e.stopPropagation(); isSamplePromptEnabled = false; }}
						>
							OFF
						</button>
					</div>
				</div>
				<div class="pl-7 flex flex-col gap-1">
					<p class="text-[11px] text-muted-foreground leading-snug">
						{t('setup.wizard.systemPromptDesc', '泥濘推奨のキャラクター対話設定（一般的な恋人との対話）を適用します。')}
					</p>
					<div class="flex items-center gap-1.5 text-[11px] text-purple-600 dark:text-purple-400/90 font-medium">
						<Tooltip.Root delayDuration={50}>
							<Tooltip.Trigger
								class="inline-flex items-center gap-1 hover:underline cursor-pointer group"
								onclick={(e) => e.stopPropagation()}
							>
								<span class="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20 group-hover:bg-purple-500/20 transition-colors">
									<Info class="w-2.5 h-2.5 shrink-0" />
								</span>
								<span>{selectedLocale === 'ja' ? '情景描写（**）のフロント連動について' : 'About scenery descriptions (**) in UI'}</span>
							</Tooltip.Trigger>
							<Tooltip.Content side="top" align="start" class="max-w-xs p-3.5 bg-popover/95 backdrop-blur-md border border-border shadow-xl text-foreground text-xs rounded-xl z-[9999]">
								<div class="font-semibold mb-1 text-primary flex items-center gap-1.5 text-xs">
									<Sparkles class="w-3.5 h-3.5" />
									{selectedLocale === 'ja' ? '情景描写 (** / * *) のフロント連動' : 'Scenery Tag Parsing'}
								</div>
								<div class="text-[11px] text-muted-foreground leading-relaxed">
									{selectedLocale === 'ja'
										? 'プロンプトに指定されている「**」（または「* *」）で囲まれた情景描写（例：*窓の外で雨足が強まる*）は、フロントエンド側で自動的に抽出・認識され、セリフとは区別された美しい情景描写として画面に演出表示されます。'
										: 'Scenery narrative enclosed in ** or * * (e.g. *rain starts falling outside*) is automatically parsed by the frontend, styled distinctly as an ambient scenery element.'}
								</div>
							</Tooltip.Content>
						</Tooltip.Root>
					</div>
				</div>
			</div>
		</div>

		<!-- Footer Action -->
		<Dialog.Footer class="border-t border-border/60 pt-2.5 flex sm:justify-end gap-2">
			<Button
				size="default"
				class="w-full sm:w-auto font-semibold shadow-md bg-gradient-to-r from-primary to-primary/90 text-primary-foreground hover:opacity-95 transition-all flex items-center justify-center gap-2 cursor-pointer"
				onclick={handleComplete}
			>
				<Check class="w-4 h-4" />
				{t('setup.wizard.completeBtn', '設定を完了して始める')}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
