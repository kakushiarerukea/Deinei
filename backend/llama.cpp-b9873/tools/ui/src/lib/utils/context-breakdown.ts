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
	isEstimated: boolean;
}

interface PrefixEstimateCache {
	key: string;
	rawSystemTokens: number;
	systemCharCount: number;
	rawSummaryTokens: number;
	summaryCharCount: number;
	rawRagTokens: number;
	ragCharCount: number;
	prefixChatTokens: number;
	prefixChatCharCount: number;
	prefixReasoningTokens: number;
	prefixReasoningCharCount: number;
	prefixToolTokens: number;
	prefixToolCharCount: number;
}

interface FullTextEstimateCache {
	key: string;
	rawSystemTokens: number;
	systemCharCount: number;
	rawSummaryTokens: number;
	summaryCharCount: number;
	rawRagTokens: number;
	ragCharCount: number;
	rawChatTokens: number;
	rawChatCharCount: number;
	rawReasoningTokens: number;
	reasoningCharCount: number;
	rawToolTokens: number;
	toolCharCount: number;
}

interface BreakdownResultCache {
	key: string;
	result: ContextBreakdownResult;
}

let cachedPrefixEstimate: PrefixEstimateCache | null = null;
let cachedFullTextEstimate: FullTextEstimateCache | null = null;
let cachedBreakdownResult: BreakdownResultCache | null = null;

function extractMessageTextAndReasoning(
	msg: DatabaseMessage,
	excludeReasoning: boolean
): { chatText: string; reasoningText: string; toolText: string } {
	let chatText = '';
	let reasoningText = '';
	let toolText = '';

	if (msg.content) {
		if (msg.content.includes('<think>') || msg.content.includes('<THINK>')) {
			const thinkMatches = msg.content.match(/<think>([\s\S]*?)<\/think>/gi);
			let cleanContent = msg.content;
			if (thinkMatches) {
				for (const tm of thinkMatches) {
					reasoningText += tm.replace(/<\/?think>/gi, '').trim() + '\n';
					cleanContent = cleanContent.replace(tm, '');
				}
			}
			chatText += cleanContent.trim() + '\n';
		} else {
			chatText += msg.content.trim() + '\n';
		}
	}
	if (!excludeReasoning && msg.reasoning_content) {
		reasoningText += msg.reasoning_content.trim() + '\n';
	}
	if (msg.extras) {
		for (const extra of msg.extras) {
			if (extra.type === 'mcp_resource' || extra.type === 'mcp_prompt') {
				toolText += JSON.stringify(extra) + '\n';
			}
		}
	}

	return { chatText, reasoningText, toolText };
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
	
	// CJK文字: Qwen2.5等では約 1文字 ≒ 0.55〜0.60 トークン
	// 英語・その他: 約 1単語 ≒ 1.3 トークン + 記号分
	const cjkTokens = Math.ceil(cjkCount * 0.60);
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
	const userPersona = currentConfig.userPersona?.toString().trim() || '';

	const safeMessagesList = Array.isArray(activeMessagesList) ? activeMessagesList : [];

	// 実測 contextUsed の決定（キャッシュキー判定のため先に解決）:
	// 1. 引数 actualContextUsed が正の値であれば最優先で使用
	// 2. なければ直近のアシスタントメッセージの timings (prompt_n + cache_n + predicted_n) から実測値を自動取得
	let resolvedActualUsed = actualContextUsed && actualContextUsed > 0 ? actualContextUsed : undefined;
	if (!resolvedActualUsed) {
		for (let i = safeMessagesList.length - 1; i >= 0; i--) {
			const m = safeMessagesList[i];
			if (m.role === 'assistant' && m.timings) {
				const promptTotal = (m.timings.prompt_n || 0) + (m.timings.cache_n || 0);
				const totalWithGen = promptTotal + (m.timings.predicted_n || 0);
				if (totalWithGen > 0) {
					resolvedActualUsed = totalWithGen;
					break;
				}
			}
		}
	}

	const convId = conversation?.id ?? '';
	const lastSummarizedIdx = conversation?.lastSummarizedIndex ?? 0;
	const summaryBlocks = conversation?.aiHistoryTextList ?? [];
	const lastSummaryBlockLen = summaryBlocks.length > 0 ? summaryBlocks[summaryBlocks.length - 1].length : 0;
	const fallbackSummaryLen = conversation?.aiHistoryText?.length ?? 0;
	const lastMsg = safeMessagesList.length > 0 ? safeMessagesList[safeMessagesList.length - 1] : undefined;
	const secondLastMsg = safeMessagesList.length > 1 ? safeMessagesList[safeMessagesList.length - 2] : undefined;

	const fullTextKey = `${convId}|${safeMessagesList.length}|${lastMsg?.id ?? ''}|${lastMsg?.content?.length ?? 0}|${lastMsg?.reasoning_content?.length ?? 0}|${lastMsg?.injectedContext?.length ?? 0}|${secondLastMsg?.id ?? ''}|${secondLastMsg?.injectedContext?.length ?? 0}|${lastSummarizedIdx}|${summaryBlocks.length}|${lastSummaryBlockLen}|${fallbackSummaryLen}|${excludeReasoning ? 1 : 0}|${userPersona.length}`;
	const resultKey = `${fullTextKey}|${contextTotal}|${resolvedActualUsed ?? 0}`;

	// PC画面のように同一フレームで複数箇所のゲージ（ChatForm / ChatScreenProcessingInfo）から呼ばれた場合は即座にキャッシュ返却
	if (cachedBreakdownResult && cachedBreakdownResult.key === resultKey) {
		return cachedBreakdownResult.result;
	}

	let rawSystemTokens = 0;
	let systemCharCount = 0;
	let rawSummaryTokens = 0;
	let summaryCharCount = 0;
	let rawRagTokens = 0;
	let ragCharCount = 0;
	let rawChatTokens = 0;
	let rawChatCharCount = 0;
	let rawReasoningTokens = 0;
	let reasoningCharCount = 0;
	let rawToolTokens = 0;
	let toolCharCount = 0;

	// ストリーミング中に onTimings で actualContextUsed だけが増加したケースでは、全文の推定トークン数をそのまま再利用（正規表現スキャン 0 回）
	if (cachedFullTextEstimate && cachedFullTextEstimate.key === fullTextKey) {
		rawSystemTokens = cachedFullTextEstimate.rawSystemTokens;
		systemCharCount = cachedFullTextEstimate.systemCharCount;
		rawSummaryTokens = cachedFullTextEstimate.rawSummaryTokens;
		summaryCharCount = cachedFullTextEstimate.summaryCharCount;
		rawRagTokens = cachedFullTextEstimate.rawRagTokens;
		ragCharCount = cachedFullTextEstimate.ragCharCount;
		rawChatTokens = cachedFullTextEstimate.rawChatTokens;
		rawChatCharCount = cachedFullTextEstimate.rawChatCharCount;
		rawReasoningTokens = cachedFullTextEstimate.rawReasoningTokens;
		reasoningCharCount = cachedFullTextEstimate.reasoningCharCount;
		rawToolTokens = cachedFullTextEstimate.rawToolTokens;
		toolCharCount = cachedFullTextEstimate.toolCharCount;
	} else {
		// 末尾メッセージ以外の過去ログ・要約・システムプロンプト・RAG のキャッシュキー
		const prefixKey = `${convId}|${safeMessagesList.length}|${secondLastMsg?.id ?? ''}|${secondLastMsg?.content?.length ?? 0}|${secondLastMsg?.injectedContext?.length ?? 0}|${lastMsg?.injectedContext?.length ?? 0}|${lastSummarizedIdx}|${summaryBlocks.length}|${lastSummaryBlockLen}|${fallbackSummaryLen}|${excludeReasoning ? 1 : 0}|${userPersona.length}`;

		const nonSystemMessages = safeMessagesList.filter((m) => m.type !== 'root' && m.role !== 'system');
		const activeRawMessages = nonSystemMessages.slice(lastSummarizedIdx);
		const prefixRawMessages = activeRawMessages.length > 0 ? activeRawMessages.slice(0, -1) : [];
		const tailRawMessage = activeRawMessages.length > 0 ? activeRawMessages[activeRawMessages.length - 1] : undefined;

		if (cachedPrefixEstimate && cachedPrefixEstimate.key === prefixKey) {
			rawSystemTokens = cachedPrefixEstimate.rawSystemTokens;
			systemCharCount = cachedPrefixEstimate.systemCharCount;
			rawSummaryTokens = cachedPrefixEstimate.rawSummaryTokens;
			summaryCharCount = cachedPrefixEstimate.summaryCharCount;
			rawRagTokens = cachedPrefixEstimate.rawRagTokens;
			ragCharCount = cachedPrefixEstimate.ragCharCount;
		} else {
			// 1. システムプロンプト & ペルソナ（会話内に実際にシステムメッセージが存在する場合のみ計上）
			let systemText = '';
			const systemMessages = safeMessagesList.filter((m) => m.role === 'system' && m.type !== 'root');
			if (systemMessages.length > 0) {
				for (const sm of systemMessages) {
					if (sm.content) {
						systemText += (systemText ? '\n' : '') + sm.content;
					}
				}
				if (userPersona && !systemText.includes(userPersona)) {
					systemText += `\n[User Persona]\n${userPersona}`;
				}
			}

			// 2. あらすじ要約ブロック
			const summaryText = summaryBlocks.join('\n\n') || conversation?.aiHistoryText || '';

			// 3. RAG 記憶 & 外部情報
			let ragText = '';
			let latestInjectedMsg: DatabaseMessage | undefined;
			for (let i = activeRawMessages.length - 1; i >= 0; i--) {
				if (activeRawMessages[i].injectedContext) {
					latestInjectedMsg = activeRawMessages[i];
					break;
				}
			}
			if (latestInjectedMsg?.injectedContext) {
				ragText += `\n${latestInjectedMsg.injectedContext}`;
			}

			let latestDynamicSystem: DatabaseMessage | undefined;
			for (let i = safeMessagesList.length - 1; i >= 0; i--) {
				const m = safeMessagesList[i];
				if (
					m.role === 'system' &&
					m.content &&
					(m.content.includes('【関連する過去の事実・記録】') ||
						m.content.includes('【関連する過去のエピソード記憶') ||
						m.content.includes('【リアルタイムWeb検索・気象データ】') ||
						m.content.includes('【現在日時】') ||
						m.content.includes('【ユーザーの沈黙'))
				) {
					latestDynamicSystem = m;
					break;
				}
			}
			if (latestDynamicSystem?.content && !ragText.includes(latestDynamicSystem.content)) {
				ragText += `\n${latestDynamicSystem.content}`;
			}

			// 4. 末尾メッセージを除く過去メッセージ群の集計
			let prefixChatText = '';
			let prefixReasoningText = '';
			let prefixToolText = '';
			for (const msg of prefixRawMessages) {
				const extracted = extractMessageTextAndReasoning(msg, excludeReasoning);
				prefixChatText += extracted.chatText;
				prefixReasoningText += extracted.reasoningText;
				prefixToolText += extracted.toolText;
			}

			rawSystemTokens = estimateTokens(systemText);
			systemCharCount = systemText.length;
			rawSummaryTokens = estimateTokens(summaryText);
			summaryCharCount = summaryText.length;
			rawRagTokens = estimateTokens(ragText);
			ragCharCount = ragText.length;

			cachedPrefixEstimate = {
				key: prefixKey,
				rawSystemTokens,
				systemCharCount,
				rawSummaryTokens,
				summaryCharCount,
				rawRagTokens,
				ragCharCount,
				prefixChatTokens: estimateTokens(prefixChatText),
				prefixChatCharCount: prefixChatText.length,
				prefixReasoningTokens: estimateTokens(prefixReasoningText),
				prefixReasoningCharCount: prefixReasoningText.length,
				prefixToolTokens: estimateTokens(prefixToolText),
				prefixToolCharCount: prefixToolText.length
			};
		}

		// 末尾1メッセージ（現在ストリーミング中のメッセージ等）のみ差分計算して加算
		let tailChatTokens = 0;
		let tailChatCharCount = 0;
		let tailReasoningTokens = 0;
		let tailReasoningCharCount = 0;
		let tailToolTokens = 0;
		let tailToolCharCount = 0;

		if (tailRawMessage) {
			const tailExtracted = extractMessageTextAndReasoning(tailRawMessage, excludeReasoning);
			tailChatTokens = estimateTokens(tailExtracted.chatText);
			tailChatCharCount = tailExtracted.chatText.length;
			tailReasoningTokens = estimateTokens(tailExtracted.reasoningText);
			tailReasoningCharCount = tailExtracted.reasoningText.length;
			tailToolTokens = estimateTokens(tailExtracted.toolText);
			tailToolCharCount = tailExtracted.toolText.length;
		}

		rawChatTokens = cachedPrefixEstimate.prefixChatTokens + tailChatTokens;
		rawChatCharCount = cachedPrefixEstimate.prefixChatCharCount + tailChatCharCount;
		rawReasoningTokens = cachedPrefixEstimate.prefixReasoningTokens + tailReasoningTokens;
		reasoningCharCount = cachedPrefixEstimate.prefixReasoningCharCount + tailReasoningCharCount;
		rawToolTokens = cachedPrefixEstimate.prefixToolTokens + tailToolTokens;
		toolCharCount = cachedPrefixEstimate.prefixToolCharCount + tailToolCharCount;

		cachedFullTextEstimate = {
			key: fullTextKey,
			rawSystemTokens,
			systemCharCount,
			rawSummaryTokens,
			summaryCharCount,
			rawRagTokens,
			ragCharCount,
			rawChatTokens,
			rawChatCharCount,
			rawReasoningTokens,
			reasoningCharCount,
			rawToolTokens,
			toolCharCount
		};
	}

	const estimatedTotal = rawSystemTokens + rawSummaryTokens + rawRagTokens + rawChatTokens + rawReasoningTokens + rawToolTokens;

	// 実測値がある場合はプロポーショナル（比率）にフィットさせて実測値と一致させる
	const hasActual = typeof resolvedActualUsed === 'number' && resolvedActualUsed > 0;
	const effectiveUsed = hasActual ? resolvedActualUsed! : estimatedTotal;
	const isEstimated = !hasActual;
	const scale = estimatedTotal > 0 ? effectiveUsed / estimatedTotal : 1;

	let systemTokens = Math.round(rawSystemTokens * scale);
	let summaryTokens = Math.round(rawSummaryTokens * scale);
	let ragTokens = Math.round(rawRagTokens * scale);
	let rawTokens = Math.round(rawChatTokens * scale);
	let reasoningTokens = Math.round(rawReasoningTokens * scale);
	let toolTokens = Math.round(rawToolTokens * scale);

	// 端数ズレの微調整（四捨五入誤差を最大セグメントで吸収）
	let totalUsedTokens = systemTokens + summaryTokens + ragTokens + rawTokens + reasoningTokens + toolTokens;
	if (hasActual && totalUsedTokens !== effectiveUsed) {
		const diff = effectiveUsed - totalUsedTokens;
		// 0でない最大セグメントに差分を加算
		if (rawTokens >= summaryTokens && rawTokens >= systemTokens) {
			rawTokens += diff;
		} else if (summaryTokens >= rawTokens && summaryTokens >= systemTokens) {
			summaryTokens += diff;
		} else {
			systemTokens += diff;
		}
		totalUsedTokens = systemTokens + summaryTokens + ragTokens + rawTokens + reasoningTokens + toolTokens;
	}

	const freeTokens = Math.max(0, contextTotal - totalUsedTokens);
	const freePercent = Math.max(0, ((freeTokens / contextTotal) * 100));

	const segments: ContextSegment[] = [
		{
			id: 'system',
			label: t('gauge.system', 'System & Settings'),
			color: '#8b5cf6', // Violet
			tokens: systemTokens,
			percent: (systemTokens / contextTotal) * 100,
			charCount: systemCharCount
		},
		{
			id: 'summary',
			label: t('gauge.summary', 'Summary'),
			color: '#3b82f6', // Blue
			tokens: summaryTokens,
			percent: (summaryTokens / contextTotal) * 100,
			charCount: summaryCharCount
		},
		{
			id: 'rag',
			label: t('gauge.rag', 'RAG & Memory'),
			color: '#10b981', // Emerald
			tokens: ragTokens,
			percent: (ragTokens / contextTotal) * 100,
			charCount: ragCharCount
		},
		{
			id: 'raw',
			label: t('gauge.raw', 'Recent Chat'),
			color: '#f59e0b', // Amber
			tokens: rawTokens,
			percent: (rawTokens / contextTotal) * 100,
			charCount: rawChatCharCount
		},
		{
			id: 'reasoning',
			label: t('gauge.reasoning', 'Reasoning'),
			color: '#ec4899', // Pink
			tokens: reasoningTokens,
			percent: (reasoningTokens / contextTotal) * 100,
			charCount: reasoningCharCount
		},
		{
			id: 'tools',
			label: t('gauge.tools', 'Tools & Files'),
			color: '#06b6d4', // Cyan
			tokens: toolTokens,
			percent: (toolTokens / contextTotal) * 100,
			charCount: toolCharCount
		}
	];

	const result: ContextBreakdownResult = {
		total: contextTotal,
		used: totalUsedTokens,
		free: freeTokens,
		freePercent,
		segments,
		isEstimated
	};

	cachedBreakdownResult = {
		key: resultKey,
		result
	};

	return result;
}
