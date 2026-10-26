/**
 * VRM 表情・感情キーワード変換モジュール
 * 
 * TTS 音声合成時の感情キーワード (Live2D 互換) を VRM 1.0 標準表情プリセットに正規化する。
 */

export type VrmStandardExpression =
	| 'happy'
	| 'angry'
	| 'sad'
	| 'relaxed'
	| 'surprised'
	| 'neutral'
	| 'aa'
	| 'ih'
	| 'ou'
	| 'ee'
	| 'oh'
	| 'blink';

/** Live2D の内部感情キーから VRM 1.0 標準プリセットへのマッピング */
export const EMOTION_TO_VRM_PRESET: Record<string, VrmStandardExpression> = {
	heart: 'happy',
	xd: 'happy',
	smile: 'relaxed',
	angry: 'angry',
	cry: 'sad',
	surprised: 'surprised',
	idle: 'neutral',
	neutral: 'neutral',
	happy: 'happy',
	sad: 'sad',
	relaxed: 'relaxed',
	blush: 'happy'
};

/**
 * 検出された感情キーまたはキーワード文字列から VRM 1.0 の有効な表情名を解決する
 */
export function resolveVrmExpression(emotionKey: string | null | undefined): VrmStandardExpression {
	if (!emotionKey) return 'neutral';
	const lower = emotionKey.toLowerCase().trim();
	return EMOTION_TO_VRM_PRESET[lower] || 'neutral';
}
