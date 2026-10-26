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

	const safeMessagesList = Array.isArray(activeMessagesList) ? activeMessagesList : [];

	// 1. システムプロンプト & ペルソナ（会話内に実際にシステムメッセージが存在する場合のみ計上）
	let systemText = '';
	const systemMessages = safeMessagesList.filter((m) => m.role === 'system' && m.type !== 'root');
	if (systemMessages.length > 0) {
		for (const sm of systemMessages) {
			if (sm.content) {
				systemText += (systemText ? '\n' : '') + sm.content;
			}
		}
		const userPersona = currentConfig.userPersona?.toString().trim();
		if (userPersona && !systemText.includes(userPersona)) {
			systemText += `\n[User Persona]\n${userPersona}`;
		}
	}

	// 2. あらすじ要約ブロック
	const summaryBlocks = conversation?.aiHistoryTextList ?? [];
	const summaryText = summaryBlocks.join('\n\n') || conversation?.aiHistoryText || '';

	// 3. RAG 記憶 & 外部情報
	// ※過去ターンの事実注入はプロンプトに残さない方針のため、
	// プロンプトに含まれる最新の動的コンテキスト（最新メッセージの injectedContext または直近の動的システムメッセージ）のみを計上する
	let ragText = '';

	const lastSummarizedIdx = conversation?.lastSummarizedIndex ?? 0;
	const nonSystemMessages = safeMessagesList.filter((m) => m.type !== 'root' && m.role !== 'system');
	const activeRawMessages = nonSystemMessages.slice(lastSummarizedIdx);

	// 未要約メッセージ群のうち最新のメッセージに記録されている動的コンテキスト（直近1回分のみ）を計上
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

	// システムメッセージのうち最新の動的情報（Web検索や想起事実）があれば計上
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

	// 4. 生の対話メッセージ & 5. 思考文 (Reasoning)
	let rawChatText = '';
	let reasoningText = '';

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

	// 実測 contextUsed の決定:
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

	// 実測値がある場合はプロポーショナル（比率）にフィットさせて実測値と完全一致させる
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
		segments,
		isEstimated
	};
}
