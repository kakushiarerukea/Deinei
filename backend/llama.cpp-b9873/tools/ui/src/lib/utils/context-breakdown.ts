import type { DatabaseConversation, DatabaseMessage } from '$lib/types/database';
import { config } from '$lib/stores/settings.svelte';
import { t } from '$lib/stores/i18n.svelte';

export interface ContextSegment {
	id: string;
	label: string;
	color: string;
	tokens: number;
	percent: number;
	charCount: number;
}

export interface ContextBreakdownResult {
	total: number;
	used: number;
	free: number;
	freePercent: number;
	segments: ContextSegment[];
}

/**
 * 日本語・多言語混在テキストのトークン数を高精度に推定
 */
export function estimateTokens(text: string): number {
	if (!text) return 0;
	// 日本語（CJK文字）と英数字・記号を分離して計算
	const cjkCount = (text.match(/[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]/g) || []).length;
	const otherText = text.replace(/[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]/g, '');
	const words = otherText.trim().split(/\s+/).filter(Boolean);
	
	// CJK文字: 約 1文字 ≒ 0.75 トークン
	// 英語・その他: 約 1単語 ≒ 1.3 トークン + 記号分
	const cjkTokens = Math.ceil(cjkCount * 0.75);
	const otherTokens = Math.ceil(words.length * 1.3) + Math.ceil(otherText.length * 0.2);
	
	return Math.max(1, cjkTokens + otherTokens);
}

/**
 * 会話データと実測ステートからコンテキストの各セグメント内訳を算出
 */
export function calculateContextBreakdown(
	conversation: DatabaseConversation | null,
	activeMessagesList: DatabaseMessage[],
	contextTotal = 16384,
	actualContextUsed?: number
): ContextBreakdownResult {
	const currentConfig = config();
	const excludeReasoning = currentConfig.excludeReasoningFromContext ?? true;

	// 1. システムプロンプト & ペルソナ
	let systemText = currentConfig.systemMessage?.toString().trim() || '';
	const userPersona = currentConfig.userPersona?.toString().trim();
	if (userPersona) {
		systemText += `\n[User Persona]\n${userPersona}`;
	}
	// システムメッセージ（DB内）も含める
	const systemMessages = activeMessagesList.filter((m) => m.role === 'system' && m.type !== 'root');
	for (const sm of systemMessages) {
		if (sm.content && !systemText.includes(sm.content.slice(0, 50))) {
			systemText += `\n${sm.content}`;
		}
	}

	// 2. あらすじ要約ブロック
	const summaryBlocks = conversation?.aiHistoryTextList ?? [];
	const summaryText = summaryBlocks.join('\n\n') || conversation?.aiHistoryText || '';

	// 3. RAG 記憶 & 外部情報（直近メッセージ内の動的コンテキストまたはセッション情報）
	let ragText = '';
	// 直近のメッセージや会話履歴から注入された RAG / Web 検索テキストを収集
	for (const msg of activeMessagesList) {
		if (msg.role === 'system' && msg.content) {
			if (
				msg.content.includes('【関連する過去の事実・記録】') ||
				msg.content.includes('【関連する過去のエピソード記憶') ||
				msg.content.includes('【リアルタイムWeb検索・気象データ】') ||
				msg.content.includes('【現在日時】') ||
				msg.content.includes('【ユーザーの沈黙')
			) {
				ragText += `\n${msg.content}`;
			}
		}
	}

	// 4. 生の対話メッセージ & 5. 思考文 (Reasoning)
	let rawChatText = '';
	let reasoningText = '';
	const lastSummarizedIdx = conversation?.lastSummarizedIndex ?? 0;

	// スライディング枠外の直近の未要約メッセージ
	const nonSystemMessages = activeMessagesList.filter((m) => m.type !== 'root' && m.role !== 'system' && !m.isHidden);
	const activeRawMessages = nonSystemMessages.slice(lastSummarizedIdx);

	for (const msg of activeRawMessages) {
		if (msg.content) {
			// <think> タグが含まれている場合は分離
			const thinkMatches = msg.content.match(/<think>([\s\S]*?)<\/think>/gi);
			let cleanContent = msg.content;
			if (thinkMatches) {
				for (const tm of thinkMatches) {
					reasoningText += tm.replace(/<\/?think>/gi, '').trim() + '\n';
					cleanContent = cleanContent.replace(tm, '');
				}
			}
			rawChatText += cleanContent.trim() + '\n';
		}
		// 個別の reasoning_content フィールド
		if (!excludeReasoning && msg.reasoning_content) {
			reasoningText += msg.reasoning_content.trim() + '\n';
		}
	}

	// 6. ツール & 添付ファイル (MCP / 添付資料)
	let toolText = '';
	for (const msg of activeRawMessages) {
		if (msg.extras) {
			for (const extra of msg.extras) {
				if (extra.type === 'mcp_resource' || extra.type === 'mcp_prompt') {
					toolText += JSON.stringify(extra) + '\n';
				}
			}
		}
	}

	// 推定トークン数の算出
	const rawSystemTokens = estimateTokens(systemText);
	const rawSummaryTokens = estimateTokens(summaryText);
	const rawRagTokens = estimateTokens(ragText);
	const rawChatTokens = estimateTokens(rawChatText);
	const rawReasoningTokens = estimateTokens(reasoningText);
	const rawToolTokens = estimateTokens(toolText);

	const estimatedTotal = rawSystemTokens + rawSummaryTokens + rawRagTokens + rawChatTokens + rawReasoningTokens + rawToolTokens;

	// 実測 contextUsed がある場合はプロポーショナル（比率）にフィットさせて完全一致させる
	const effectiveUsed = actualContextUsed && actualContextUsed > 0 ? actualContextUsed : estimatedTotal;
	const scale = estimatedTotal > 0 ? effectiveUsed / estimatedTotal : 1;

	const systemTokens = Math.round(rawSystemTokens * scale);
	const summaryTokens = Math.round(rawSummaryTokens * scale);
	const ragTokens = Math.round(rawRagTokens * scale);
	const rawTokens = Math.round(rawChatTokens * scale);
	const reasoningTokens = Math.round(rawReasoningTokens * scale);
	const toolTokens = Math.round(rawToolTokens * scale);

	const totalUsedTokens = systemTokens + summaryTokens + ragTokens + rawTokens + reasoningTokens + toolTokens;
	const freeTokens = Math.max(0, contextTotal - totalUsedTokens);
	const freePercent = Math.max(0, ((freeTokens / contextTotal) * 100));

	const segments: ContextSegment[] = [
		{
			id: 'system',
			label: t('gauge.system', 'System & Settings'),
			color: '#8b5cf6', // Violet
			tokens: systemTokens,
			percent: (systemTokens / contextTotal) * 100,
			charCount: systemText.length
		},
		{
			id: 'summary',
			label: t('gauge.summary', 'Summary'),
			color: '#3b82f6', // Blue
			tokens: summaryTokens,
			percent: (summaryTokens / contextTotal) * 100,
			charCount: summaryText.length
		},
		{
			id: 'rag',
			label: t('gauge.rag', 'RAG & Memory'),
			color: '#10b981', // Emerald
			tokens: ragTokens,
			percent: (ragTokens / contextTotal) * 100,
			charCount: ragText.length
		},
		{
			id: 'raw',
			label: t('gauge.raw', 'Recent Chat'),
			color: '#f59e0b', // Amber
			tokens: rawTokens,
			percent: (rawTokens / contextTotal) * 100,
			charCount: rawChatText.length
		},
		{
			id: 'reasoning',
			label: t('gauge.reasoning', 'Reasoning'),
			color: '#ec4899', // Pink
			tokens: reasoningTokens,
			percent: (reasoningTokens / contextTotal) * 100,
			charCount: reasoningText.length
		},
		{
			id: 'tools',
			label: t('gauge.tools', 'Tools & Files'),
			color: '#06b6d4', // Cyan
			tokens: toolTokens,
			percent: (toolTokens / contextTotal) * 100,
			charCount: toolText.length
		}
	];

	return {
		total: contextTotal,
		used: totalUsedTokens,
		free: freeTokens,
		freePercent,
		segments
	};
}
