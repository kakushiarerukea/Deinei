import { browser } from '$app/environment';

export type SupportedLocale = 'en' | 'ja';

export const SUPPORTED_LOCALES: { code: SupportedLocale; label: string; nativeLabel: string }[] = [
	{ code: 'ja', label: 'Japanese', nativeLabel: '日本語' },
	{ code: 'en', label: 'English', nativeLabel: 'English' }
];

export const translations: Record<SupportedLocale, Record<string, string>> = {
	en: {
		// --- General & Settings ---
		'settings.language': 'Language',
		'settings.languageDesc': '[Advanced] Select your preferred interface language (Experimental / Under active development).',
		'settings.theme': 'Theme',
		'settings.themeColor': 'Theme Color (Hue)',
		'settings.themeColorDesc': 'Customize base accent hue from 0° to 360°.',
		'settings.reset': 'Reset',
		'settings.autoSummarize': 'Auto Summarize',
		'settings.autoSummarizeHelp': 'Automatically summarizes conversations over 30 messages and extracts personal facts into long-term memory.',

		// --- Web Search ---
		'search.enabled': 'Web Search: ON',
		'search.disabled': 'Web Search: OFF (KV Cache Preserved)',
		'search.toggleTooltip': 'Toggle Web Search (DuckDuckGo / Weather)',

		// --- RAG & Memory Panel ---
		'rag.title': 'RAG & Memory',
		'rag.addMemory': 'Add Memory',
		'rag.inputPlaceholder': 'Enter memory text...',
		'rag.deduplicate': 'Deduplicate Memories',
		'rag.deduplicating': 'Deduplicating...',
		'rag.reasoningLogs': 'Reasoning Logs',
		'rag.summaryReasoning': '1. Summary Reasoning',
		'rag.summaryOutput': '1-2. Summary Final Output',
		'rag.factReasoning': '2. Fact Extraction Reasoning',
		'rag.factOutput': '2-2. Fact Extraction Final Output',
		'rag.open': 'Open',
		'rag.close': 'Close',
		'rag.episodes': 'Recalled Episodes',
		'rag.viewEpisode': 'View Full Episode',
		'rag.clearAll': 'Clear All Memories',

		// --- TTS Panel ---
		'tts.title': 'TTS & Voice Settings',
		'tts.enableTts': 'Speech Synthesis (TTS)',
		'tts.voiceChat': 'Voice Conversation (STT)',
		'tts.syncSpeech': 'Sync Text with Speech (Sequential Display)',
		'tts.speed': 'Voice Speed',
		'tts.test': 'Test Voice',
		'tts.testPhrase': 'This is a voice test. Are lip sync and expressions working?',

		// --- Debug & Monitoring Panel ---
		'debug.title': 'Debug',
		'debug.summaryStatus': 'Summary Status',
		'debug.conversationId': 'Conversation ID:',
		'debug.autoSummarize': 'Auto Summarize:',
		'debug.on': 'ON (Enabled)',
		'debug.off': 'OFF (Disabled)',
		'debug.totalMessages': 'Total Messages:',
		'debug.turns': 'turns',
		'debug.nextSummary': 'Next Summary in:',
		'debug.inTurns': '{n} turns left',
		'debug.lastSummaryCount': 'Last Summarized Count:',
		'debug.summaryBlocksCount': 'Summary Blocks:',
		'debug.slidingWindow': 'Sliding Window:',
		'debug.active': 'Active',
		'debug.inactive': 'Inactive',
		'debug.forceRun': 'Force Run Summary Now',
		'debug.rebuild': 'Rebuild Conversation Memory',
		'debug.typewriterTitle': 'Typewriter Animation',
		'debug.typewriterDesc': 'Displays pre-read paragraphs character-by-character sequentially.',
		'debug.speed': 'Display Speed:',
		'debug.reset': 'Reset',
		'debug.promptSettings': 'Summary & RAG Settings',
		'debug.summarySettings': '📝 Summary Prompt Settings',
		'debug.systemPrompt': 'System Prompt:',
		'debug.factSettings': '🧠 Fact Extraction Settings',
		'debug.restoreDefaults': 'Restore Defaults',
		'debug.summaryBlocks': 'Summary Blocks Content',
		'debug.oldestSummary': 'Oldest Summary',
		'debug.noSummaryBlocks': 'No summary blocks yet.',
		'debug.noSummaryBlocksHelp': '(Conversation has fewer than 30 turns or not generated)',
		'debug.archivedEpisodes': 'Pushed Archived Episodes',
		'debug.archivedStatusCompleted': 'RAG Indexed',
		'debug.archivedStatusPending': 'Pending Index',
		'debug.archivedTime': 'Archived at:',
		'debug.noArchived': 'No archived episodes yet.',
		'debug.noArchivedHelp': '(Episodes are accumulated here when summary blocks exceed limit)',
		'debug.lastRecalledTitle': '🧠 Recently Recalled Memories (RAG / Web)',
		'debug.recalledFacts': '🟢 Fact Memories:',
		'debug.recalledEpisodes': '🟣 Parent-Child Retrieval (Episodes):',
		'debug.recalledWeb': '🌐 Web Search Results:',
		'debug.noRecalled': 'No recent memory retrieval logs.',
		'debug.noRecalledHelp': '(Triggered RAG or parent-child retrieval results will appear here)',

		// --- Toast Notifications ---
		'toast.rebuildingMemory': 'Rebuilding chat memories...',
		'toast.rebuildingMemoryProgress': 'Rebuilding chat memories... ({current} / {total})',
		'toast.rebuildSuccess': 'Chat memories and summary rebuilt successfully!',
		'toast.rebuildError': 'An error occurred while rebuilding memories.',
		'toast.organizingMemory': 'Organizing memory... {seconds}s',

		// --- Chat Interface ---
		'chat.recalledMemories': 'Recalled Memories',
		'chat.items': 'items',
		'chat.facts': 'Facts',
		'chat.episodes': 'Episodes',
		'chat.web': 'Web Search Results',
		'chat.copy': 'Copy',
		'chat.copied': 'Copied!',
		'chat.next': 'Next',
		'chat.skipAll': 'Skip All',
		'chat.syncing': 'Displaying text in sync with voice...',
		'chat.transcribing': 'Transcribing audio...',
		'chat.stopRecord': 'Stop recording & transcribe',
		'chat.startRecord': 'Voice input (STT)',
		'chat.freeSpace': 'Free:',
		'chat.total': 'Total',
		'chat.placeholder': 'Type a message...',

		// --- Context Gauge ---
		'gauge.free': 'Free:',
		'gauge.total': 'Total',
		'gauge.system': 'System & Settings',
		'gauge.summary': 'Summary',
		'gauge.rag': 'RAG & Memory',
		'gauge.raw': 'Recent Chat',
		'gauge.reasoning': 'Reasoning',
		'gauge.tools': 'Tools & Files',
		'gauge.estimated': 'Est.',

		// --- Live2D ---
		'live2d.silverHair': '銀髪の女の子',
		'live2d.brownHair': '茶髪の癒し系お姉さん',
		'live2d.editTouchZones': 'Edit touch interaction zones',
		'live2d.outfit': 'Outfit',

		// --- Initial Setup Wizard ---
		'setup.wizard.title': 'Welcome to Deinei!',
		'setup.wizard.subtitle': 'Initial Setup Wizard',
		'setup.wizard.desc': 'Configure basic options to match your preference (can be changed anytime later).',
		'setup.wizard.language': '1. Display Language',
		'setup.wizard.languageDesc': 'Select your preferred interface language.',
		'setup.wizard.tts': '2. Speech Synthesis (Irodori-TTS)',
		'setup.wizard.ttsDesc': 'Automatically reads aloud the assistant response using Irodori-TTS.',
		'setup.wizard.ttsSync': '3. Sync Text with Speech',
		'setup.wizard.ttsSyncDesc': 'Displays sentences one by one in sync with speech playback (Turn off to show full text instantly).',
		'setup.wizard.webSearch': '4. Real-time Web Search (DuckDuckGo)',
		'setup.wizard.webSearchDesc': 'Searches for the latest news and weather via DuckDuckGo. Note: Triggers automatically only when your message contains keywords such as "search", "weather", "news", or "latest".',
		'setup.wizard.systemPrompt': '5. Sample System Prompt',
		'setup.wizard.systemPromptDesc': 'Applies the recommended dialogue settings (casual conversation with a lover).',
		'setup.wizard.completeBtn': 'Save & Get Started',
		// --- Sidebar Navigation ---
		'sidebar.newChat': 'New chat',
		'sidebar.search': 'Search',
		'sidebar.mcpServers': 'MCP Servers',
		'sidebar.settings': 'Settings',
		'sidebar.ragMemory': 'RAG Memory & Summary',
		'sidebar.ttsSettings': 'TTS & Voice Settings',
		'sidebar.debug': 'Debug',
		'sidebar.wizard': 'Initial Setup Wizard',
		'sidebar.searchPlaceholder': 'Search conversations...',
		'sidebar.recentConversations': 'Recent conversations',
		'sidebar.pinned': 'Pinned',
		'sidebar.noConversations': 'No conversations yet',
		'sidebar.noResults': 'No results found',
		'sidebar.searchResults': 'Search results',
		'sidebar.startTyping': 'Start typing to see results',
		'sidebar.moreActions': 'More actions',
		'sidebar.pin': 'Pin',
		'sidebar.unpin': 'Unpin',
		'sidebar.edit': 'Rename',
		'sidebar.export': 'Export',
		'sidebar.delete': 'Delete',
		'sidebar.parentConversation': 'See parent conversation',
		'sidebar.stopGeneration': 'Stop generation',
		'sidebar.renamePrompt': 'Rename conversation',
		'sidebar.deleteConfirm': 'Delete "{name}"? This action cannot be undone.',
		'sidebar.openSidebar': 'Open Sidebar',
		'sidebar.closeSidebar': 'Close Sidebar',
		'sidebar.openMenu': 'Open Menu',
		// --- Initial Setup Wizard Custom Prompt ---
		'setup.wizard.promptConfirmTitle': 'Create Custom System Prompt?',
		'setup.wizard.promptConfirmDesc': 'You turned off the sample prompt. Would you like to enter your own custom system message (system prompt) now?',
		'setup.wizard.promptConfirmCreate': 'Enter Custom Prompt',
		'setup.wizard.promptConfirmSkip': 'Start Without Prompt',
		'setup.wizard.promptInputTitle': 'Custom System Prompt',
		'setup.wizard.promptInputDesc': 'Enter instructions, persona, or rules for the AI. You can always change this later in Settings.',
		'setup.wizard.promptInputPlaceholder': 'e.g.\nYou are a helpful and polite AI assistant.\nName: Nova\nTone: Professional and friendly',
		'setup.wizard.promptInputApply': 'Apply',
		'setup.wizard.promptInputCancel': 'Cancel',
		'setup.wizard.customPromptSet': 'Custom prompt configured ({chars} chars)',
		'setup.wizard.noPromptSet': 'No system prompt (Plain AI)',
		'setup.wizard.editCustomPrompt': 'Edit Prompt',
		'setup.wizard.enterCustomPrompt': 'Enter Prompt'
	},
	ja: {
		// --- General & Settings ---
		'settings.language': '言語 (Language)',
		'settings.languageDesc': '【Advanced 機能】表示言語を選択します（開発段階・実験的機能）。',
		'settings.theme': 'テーマ (Theme)',
		'settings.themeColor': 'テーマカラー (色相)',
		'settings.themeColorDesc': 'アプリ全体の基本アクセントカラー（色相）を 0° 〜 360° でカスタマイズできます。',
		'settings.reset': '初期値に戻す',
		'settings.autoSummarize': 'Auto Summarize (会話の自動整理とあらすじ化)',
		'settings.autoSummarizeHelp': '未要約メッセージが30件を超えた場合に自動要約（あらすじ）を実行し、同時に会話から長期記憶への自動事実抽出・保存を行います。',

		// --- Web Search ---
		'search.enabled': 'Web検索: ON',
		'search.disabled': 'Web検索: OFF (KVキャッシュ完全維持)',
		'search.toggleTooltip': 'Web検索・天気取得のオン/オフ切り替え',

		// --- RAG & Memory Panel ---
		'rag.title': 'RAG & 長期記憶',
		'rag.addMemory': '記憶を追加',
		'rag.inputPlaceholder': '保存する記憶を入力...',
		'rag.deduplicate': '重複記憶を統合整理 (Deduplicate)',
		'rag.deduplicating': '整理プロセスを実行中...',
		'rag.reasoningLogs': '推論ログ (Reasoning Logs)',
		'rag.summaryReasoning': '1. あらすじ要約の推論ログ',
		'rag.summaryOutput': '1-2. あらすじ要約の最終出力',
		'rag.factReasoning': '2. 事実抽出の推論ログ',
		'rag.factOutput': '2-2. 事実抽出の最終出力',
		'rag.open': '開く',
		'rag.close': '閉じる',
		'rag.episodes': '思い出エピソード',
		'rag.viewEpisode': '📖 当時のエピソード全文を見る',
		'rag.clearAll': 'すべての記憶を完全に消去',

		// --- TTS Panel ---
		'tts.title': '音声・TTS設定',
		'tts.enableTts': '音声読み上げ (TTS)',
		'tts.voiceChat': '音声自動対話 (STT)',
		'tts.syncSpeech': '音声同期順次表示 (Sync Speech)',
		'tts.speed': '読み上げ速度',
		'tts.test': '音声テスト',
		'tts.testPhrase': '音声テストです。リップシンクと表情が動いていますか？',

		// --- Debug & Monitoring Panel ---
		'debug.title': 'Debug',
		'debug.summaryStatus': '要約ステータス (Summary Status)',
		'debug.conversationId': '会話ID:',
		'debug.autoSummarize': '自動要約 (Auto):',
		'debug.on': 'ON (有効)',
		'debug.off': 'OFF (無効)',
		'debug.totalMessages': '現在の総メッセージ数:',
		'debug.turns': 'ターン',
		'debug.nextSummary': '次の自動要約まで:',
		'debug.inTurns': 'あと {n} ターン',
		'debug.lastSummaryCount': '最後の要約メッセージ数:',
		'debug.summaryBlocksCount': '要約ブロック数:',
		'debug.slidingWindow': 'スライディング適用:',
		'debug.active': '有効 (Active)',
		'debug.inactive': '無効 (Inactive)',
		'debug.forceRun': '今すぐ要約を実行 (Force Run)',
		'debug.rebuild': '会話全体の記憶を再構成 (Rebuild)',
		'debug.typewriterTitle': 'タイプライター風テキスト開示',
		'debug.typewriterDesc': '先読み段落の開示時に一文字ずつタイプライター風に順次表示します。',
		'debug.speed': '表示速度 (Speed):',
		'debug.reset': 'リセット',
		'debug.promptSettings': '要約 & RAG抽出 設定',
		'debug.summarySettings': '📝 あらすじ要約 設定',
		'debug.systemPrompt': 'システムプロンプト:',
		'debug.factSettings': '🧠 RAG事実抽出 設定',
		'debug.restoreDefaults': 'デフォルト値に戻す',
		'debug.summaryBlocks': '要約ブロック内容',
		'debug.oldestSummary': '最古のあらすじ',
		'debug.noSummaryBlocks': '要約ブロックがありません。',
		'debug.noSummaryBlocksHelp': '(会話が30ターン未満、または未生成)',
		'debug.archivedEpisodes': '押し出しアーカイブ済みエピソード',
		'debug.archivedStatusCompleted': 'RAG登録済',
		'debug.archivedStatusPending': '登録待機中',
		'debug.archivedTime': '忘却日時:',
		'debug.noArchived': 'アーカイブされたエピソードはありません。',
		'debug.noArchivedHelp': '(要約ブロックが上限を超過するとここに蓄積されます)',
		'debug.lastRecalledTitle': '🧠 直近想起された記憶 (RAG / Web)',
		'debug.recalledFacts': '🟢 Fact 記憶:',
		'debug.recalledEpisodes': '🟣 親子リトリーバル (エピソード):',
		'debug.recalledWeb': '🌐 Web 検索結果:',
		'debug.noRecalled': '直近の想起ログはありません。',
		'debug.noRecalledHelp': '(質問時にRAGや親子リトリーバルが発火するとここに表示されます)',

		// --- Toast Notifications ---
		'toast.rebuildingMemory': 'チャットの記憶を再構成中...',
		'toast.rebuildingMemoryProgress': 'チャットの記憶を再構成中... ({current} / {total})',
		'toast.rebuildSuccess': 'チャットの記憶とあらすじの再構成が完了しました！',
		'toast.rebuildError': '記憶の再構成中にエラーが発生しました',
		'toast.organizingMemory': '記憶を整理中... {seconds}秒',

		// --- Chat Interface ---
		'chat.recalledMemories': '想起された記憶',
		'chat.items': '件',
		'chat.facts': '事実記憶 (Fact)',
		'chat.episodes': '思い出エピソード',
		'chat.web': 'リアルタイム Web 検索',
		'chat.copy': 'コピー',
		'chat.copied': '✓ コピー済',
		'chat.next': '次へ (Next)',
		'chat.skipAll': '全文スキップ',
		'chat.syncing': '🗣️ ボイス再生に合わせてテキストを順次表示中...',
		'chat.transcribing': '音声をテキストに変換中...',
		'chat.stopRecord': '録音を停止してテキスト化',
		'chat.startRecord': '音声入力 (STT)',
		'chat.freeSpace': '空き容量:',
		'chat.total': '計',
		'chat.placeholder': 'メッセージを入力...',

		// --- Context Gauge ---
		'gauge.free': '空き容量:',
		'gauge.total': '計',
		'gauge.system': 'システム・設定',
		'gauge.summary': 'あらすじ要約',
		'gauge.rag': 'RAG・記憶・検索',
		'gauge.raw': '直近の会話ログ',
		'gauge.reasoning': '思考文 (Reasoning)',
		'gauge.tools': 'ツール・添付資料',
		'gauge.estimated': '推定',

		// --- Live2D ---
		'live2d.silverHair': '銀髪の女の子',
		'live2d.brownHair': '茶髪の癒し系お姉さん',
		'live2d.editTouchZones': 'タッチ領域（なでなで判定）を編集',
		'live2d.outfit': '衣装',

		// --- Initial Setup Wizard ---
		'setup.wizard.title': '泥濘 (Deinei) へようこそ！',
		'setup.wizard.subtitle': '初期設定ウィザード',
		'setup.wizard.desc': 'あなたの好みに合わせて基本機能を簡単に設定できます（※後からいつでも各パネルや設定から変更可能です）。',
		'setup.wizard.language': '1. 表示言語 (Language)',
		'setup.wizard.languageDesc': 'UIの表示言語を選択してください。',
		'setup.wizard.tts': '2. 音声合成 (Irodori-TTS)',
		'setup.wizard.ttsDesc': 'アシスタントの返答をリアルタイム音声合成で自動読み上げします。',
		'setup.wizard.ttsSync': '3. 音声と文章のシンク表示',
		'setup.wizard.ttsSyncDesc': '音声の読み上げ進行に合わせて、画面上の文章を1文ずつ字幕のように同期表示します（OFFにすると全文が一括で表示されます）。',
		'setup.wizard.webSearch': '4. リアルタイムWeb検索 (DuckDuckGo)',
		'setup.wizard.webSearchDesc': '最新ニュースや天気などをDuckDuckGoで検索します。※常時発火ではなく、「検索」「調べ」「最新の」「天気」「ニュース」などのキーワード発言時のみ自動で検索を実行します。',
		'setup.wizard.systemPrompt': '5. サンプルシステムプロンプト',
		'setup.wizard.systemPromptDesc': '泥濘推奨のキャラクター対話設定（一般的な恋人との対話）を適用します。',
		'setup.wizard.completeBtn': '設定を完了して始める',
		// --- Sidebar Navigation ---
		'sidebar.newChat': '新規チャット',
		'sidebar.search': '会話を検索',
		'sidebar.mcpServers': 'MCP サーバー',
		'sidebar.settings': '設定',
		'sidebar.ragMemory': 'RAG & 長期記憶',
		'sidebar.ttsSettings': '音声・TTS設定',
		'sidebar.debug': 'デバッグ・監視',
		'sidebar.wizard': '初期設定ウィザード',
		'sidebar.searchPlaceholder': '会話を検索...',
		'sidebar.recentConversations': '最近の会話',
		'sidebar.pinned': 'ピン留め',
		'sidebar.noConversations': '会話履歴がありません',
		'sidebar.noResults': '該当する会話が見つかりません',
		'sidebar.searchResults': '検索結果',
		'sidebar.startTyping': 'キーワードを入力して検索',
		'sidebar.moreActions': '操作メニュー',
		'sidebar.pin': 'ピン留めする',
		'sidebar.unpin': 'ピン留めを解除',
		'sidebar.edit': '名前を変更',
		'sidebar.export': 'エクスポート',
		'sidebar.delete': '削除',
		'sidebar.parentConversation': '親の会話を見る',
		'sidebar.stopGeneration': '生成を停止',
		'sidebar.renamePrompt': '会話の新しい名前を入力してください',
		'sidebar.deleteConfirm': '「{name}」を削除しますか？この操作は取り消せません。',
		'sidebar.openSidebar': 'サイドバーを開く',
		'sidebar.closeSidebar': 'サイドバーを閉じる',
		'sidebar.openMenu': 'メニューを開く',
		// --- Initial Setup Wizard Custom Prompt ---
		'setup.wizard.promptConfirmTitle': '新規システムメッセージを作成しますか？',
		'setup.wizard.promptConfirmDesc': 'サンプルプロンプト（恋人設定）をオフにしました。独自のキャラクター設定や前提指示（システムプロンプト）を今すぐ入力・作成しますか？',
		'setup.wizard.promptConfirmCreate': '新規プロンプトを入力する',
		'setup.wizard.promptConfirmSkip': 'プロンプトなしで開始',
		'setup.wizard.promptInputTitle': 'カスタムシステムプロンプトの入力',
		'setup.wizard.promptInputDesc': 'AIの性格、口調、役割、前提条件などの指示を入力してください。後から設定画面でもいつでも変更できます。',
		'setup.wizard.promptInputPlaceholder': '例:\nあなたは親切で落ち着いたAIアシスタントです。\n名前: ○○\n一人称: 私\n丁寧な言葉遣いで回答してください。',
		'setup.wizard.promptInputApply': '適用する',
		'setup.wizard.promptInputCancel': 'キャンセル',
		'setup.wizard.customPromptSet': 'カスタム設定中 ({chars}文字)',
		'setup.wizard.noPromptSet': 'システムプロンプトなし（まっさらな状態）',
		'setup.wizard.editCustomPrompt': '編集する',
		'setup.wizard.enterCustomPrompt': '新規プロンプトを入力'
	}
};

class I18nStore {
	private _locale = $state<SupportedLocale>('ja');

	constructor() {
		if (browser) {
			const saved = localStorage.getItem('deining_language') as SupportedLocale | null;
			if (saved && (saved === 'en' || saved === 'ja')) {
				this._locale = saved;
			} else {
				const navLang = typeof navigator !== 'undefined' ? navigator.language : '';
				this._locale = navLang.startsWith('en') ? 'en' : 'ja';
			}
			document.documentElement.lang = this._locale;
		}
	}

	get currentLocale(): SupportedLocale {
		return this._locale;
	}

	setLocale(locale: SupportedLocale) {
		this._locale = locale;
		if (browser) {
			localStorage.setItem('deining_language', locale);
			document.documentElement.lang = locale;
			window.dispatchEvent(new CustomEvent('deiningLanguageChanged', { detail: { locale } }));
		}
	}

	t(key: string, params?: Record<string, string | number> | string, fallback?: string): string {
		let text = '';
		const dict = translations[this._locale];
		if (dict && dict[key] !== undefined) {
			text = dict[key];
		} else if (this._locale !== 'en' && translations.en[key] !== undefined) {
			text = translations.en[key];
		} else {
			text = typeof params === 'string' ? params : (fallback !== undefined ? fallback : key);
		}

		if (typeof params === 'object' && params !== null) {
			for (const [k, v] of Object.entries(params)) {
				text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v));
			}
		}
		return text;
	}
}

export const i18n = new I18nStore();

export function t(key: string, params?: Record<string, string | number> | string, fallback?: string): string {
	return i18n.t(key, params, fallback);
}
