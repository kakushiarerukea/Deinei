<script lang="ts">
	import { calculateContextBreakdown, type ContextSegment } from '$lib/utils/context-breakdown';
	import { activeConversation, activeMessages } from '$lib/stores/conversations.svelte';
	import type { DatabaseMessage } from '$lib/types/database';
	import { t } from '$lib/stores/i18n.svelte';

	interface Props {
		contextTotal?: number;
		contextUsed?: number;
		outputTokensUsed?: number;
		outputTokensMax?: number;
		tokensPerSecond?: number;
	}

	let {
		contextTotal = 16384,
		contextUsed = 0,
		outputTokensUsed = 0,
		outputTokensMax = -1,
		tokensPerSecond = 0
	}: Props = $props();

	const conv = $derived(activeConversation());
	const messages = $derived(activeMessages() as DatabaseMessage[]);

	const breakdown = $derived(
		calculateContextBreakdown(conv, messages, contextTotal, contextUsed)
	);

	// ホバー中のセグメント ID
	let hoveredSegmentId = $state<string | null>(null);

	// 表示対象のセグメント（トークン数 > 0 のみ）
	const activeSegments = $derived(
		breakdown.segments.filter((s) => s.tokens > 0)
	);

	function formatNumber(num: number): string {
		return num.toLocaleString();
	}
</script>

<div class="context-gauge-container">
	<!-- 1. メインのマルチカラースタックゲージバー -->
	<div class="gauge-bar-wrapper">
		<div class="gauge-bar">
			{#each activeSegments as seg (seg.id)}
				<div
					class="gauge-segment"
					style="width: {seg.percent}%; background-color: {seg.color};"
					class:active={hoveredSegmentId === seg.id}
					onmouseenter={() => (hoveredSegmentId = seg.id)}
					onmouseleave={() => (hoveredSegmentId = null)}
					role="presentation"
				>
					<!-- ツールチップ -->
					{#if hoveredSegmentId === seg.id}
						<div class="gauge-tooltip backdrop-blur-md">
							<span class="tooltip-dot" style="background-color: {seg.color};"></span>
							<span class="tooltip-label">{seg.label}:</span>
							<span class="tooltip-val font-semibold">{formatNumber(seg.tokens)} tokens</span>
							<span class="tooltip-percent">({seg.percent.toFixed(1)}%)</span>
						</div>
					{/if}
				</div>
			{/each}

			<!-- 空き容量 (Free) セグメント -->
			<div
				class="gauge-segment free-segment"
				style="width: {breakdown.freePercent}%;"
				class:active={hoveredSegmentId === 'free'}
				onmouseenter={() => (hoveredSegmentId = 'free')}
				onmouseleave={() => (hoveredSegmentId = null)}
				role="presentation"
			>
				{#if hoveredSegmentId === 'free'}
					<div class="gauge-tooltip backdrop-blur-md">
						<span class="tooltip-dot bg-muted-foreground/50"></span>
						<span class="tooltip-label">{t('gauge.free', 'Free:')}</span>
						<span class="tooltip-val font-semibold">{formatNumber(breakdown.free)} tokens</span>
						<span class="tooltip-percent">({breakdown.freePercent.toFixed(1)}%)</span>
					</div>
				{/if}
			</div>
		</div>
	</div>

	<!-- 2. サマリー ＆ ミニ凡例 (Legend) -->
	<div class="gauge-legend-wrapper">
		<!-- 左側: 内訳ミニタグ一覧 -->
		<div class="legend-tags">
			{#each activeSegments as seg (seg.id)}
				<div
					class="legend-item"
					class:highlighted={hoveredSegmentId === seg.id}
					onmouseenter={() => (hoveredSegmentId = seg.id)}
					onmouseleave={() => (hoveredSegmentId = null)}
					role="presentation"
				>
					<span class="legend-dot" style="background-color: {seg.color};"></span>
					<span class="legend-name">{seg.label}</span>
					<span class="legend-count">{formatNumber(seg.tokens)}t</span>
				</div>
			{/each}

			<!-- 全体Contextサマリー -->
			<div
				class="legend-item total-item"
				class:highlighted={hoveredSegmentId === 'free'}
				onmouseenter={() => (hoveredSegmentId = 'free')}
				onmouseleave={() => (hoveredSegmentId = null)}
				role="presentation"
			>
				<span class="legend-dot bg-muted-foreground/40"></span>
				<span class="legend-name">{t('gauge.free', 'Free:')} {formatNumber(breakdown.free)}t</span>
				<span class="legend-percent text-[10px] text-muted-foreground/70">
					({t('gauge.total', 'Total')} {formatNumber(breakdown.used)} / {formatNumber(breakdown.total)}t · {(100 - breakdown.freePercent).toFixed(1)}%)
				</span>
			</div>
		</div>

		<!-- 右側: 出力トークン & 生成速度 -->
		<div class="meta-stats">
			{#if outputTokensUsed > 0}
				<span class="meta-stat-item">
					Output: {outputTokensUsed}{outputTokensMax > 0 ? `/${outputTokensMax}` : '/∞'}
				</span>
			{/if}
			{#if tokensPerSecond > 0}
				<span class="meta-stat-item speed-stat">
					{tokensPerSecond.toFixed(1)} t/s
				</span>
			{/if}
		</div>
	</div>
</div>

<style>
	.context-gauge-container {
		display: flex;
		flex-direction: column;
		gap: 0.375rem;
		width: 100%;
		max-width: 52rem;
		margin: 0 auto;
		user-select: none;
	}

	.gauge-bar-wrapper {
		width: 100%;
		padding: 2px 0;
	}

	.gauge-bar {
		display: flex;
		width: 100%;
		height: 6px;
		background: rgba(0, 0, 0, 0.4);
		border-radius: 9999px;
		overflow: visible;
		position: relative;
		border: 1px solid rgba(255, 255, 255, 0.08);
		box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.3);
		transition: height 0.2s ease;
	}

	.gauge-bar:hover {
		height: 8px;
	}

	.gauge-segment {
		height: 100%;
		position: relative;
		transition: width 0.4s cubic-bezier(0.4, 0, 0.2, 1), filter 0.2s ease;
		cursor: pointer;
	}

	.gauge-segment:first-child {
		border-top-left-radius: 9999px;
		border-bottom-left-radius: 9999px;
	}

	.gauge-segment:last-child {
		border-top-right-radius: 9999px;
		border-bottom-right-radius: 9999px;
	}

	.gauge-segment.active {
		filter: brightness(1.25) saturate(1.2);
		z-index: 20;
	}

	.free-segment {
		background: rgba(255, 255, 255, 0.06);
	}

	/* ツールチップ */
	.gauge-tooltip {
		position: absolute;
		bottom: calc(100% + 8px);
		left: 50%;
		transform: translateX(-50%);
		display: flex;
		align-items: center;
		gap: 0.375rem;
		background: rgba(15, 23, 42, 0.92);
		border: 1px solid rgba(255, 255, 255, 0.15);
		color: #f8fafc;
		font-size: 0.6875rem;
		padding: 0.25rem 0.5rem;
		border-radius: 0.375rem;
		white-space: nowrap;
		pointer-events: none;
		z-index: 50;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
		animation: tooltip-pop 0.15s cubic-bezier(0.16, 1, 0.3, 1);
	}

	@keyframes tooltip-pop {
		from {
			opacity: 0;
			transform: translateX(-50%) translateY(4px);
		}
		to {
			opacity: 1;
			transform: translateX(-50%) translateY(0);
		}
	}

	.tooltip-dot {
		width: 6px;
		height: 6px;
		border-radius: 9999px;
	}

	/* 凡例 (Legend) */
	.gauge-legend-wrapper {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 0.5rem;
		font-size: 0.6875rem;
		color: var(--muted-foreground);
		padding: 0 0.25rem;
	}

	.legend-tags {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.625rem;
	}

	.legend-item {
		display: flex;
		align-items: center;
		gap: 0.25rem;
		cursor: pointer;
		transition: color 0.15s ease, opacity 0.15s ease;
		opacity: 0.85;
	}

	.legend-item:hover,
	.legend-item.highlighted {
		opacity: 1;
		color: var(--foreground);
	}

	.legend-dot {
		width: 5px;
		height: 5px;
		border-radius: 9999px;
	}

	.legend-count {
		font-family: ui-monospace, SFMono-Regular, monospace;
		font-size: 0.625rem;
		opacity: 0.8;
	}

	.total-item {
		opacity: 0.65;
	}

	.meta-stats {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-family: ui-monospace, SFMono-Regular, monospace;
		font-size: 0.6875rem;
	}

	.meta-stat-item {
		background: rgba(255, 255, 255, 0.04);
		padding: 0.125rem 0.375rem;
		border-radius: 0.25rem;
		border: 1px solid rgba(255, 255, 255, 0.06);
	}

	.speed-stat {
		color: var(--primary);
	}
</style>
