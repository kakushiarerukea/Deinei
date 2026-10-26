<script lang="ts">
	import { Card } from '$lib/components/ui/card';
	import { ChatAttachmentsList, MarkdownContent } from '$lib/components/app';
	import { config } from '$lib/stores/settings.svelte';
	import type { DatabaseMessageExtra } from '$lib/types/database';

	interface Props {
		content: string;
		attachments?: DatabaseMessageExtra[];
		renderMarkdown?: boolean;
		textColorClass?: string;
		cardBgClass?: string;
		maxHeightStyle?: string;
	}

	let {
		content,
		attachments = [],
		renderMarkdown = false,
		textColorClass = 'text-foreground',
		cardBgClass = 'dark:bg-primary/15',
		maxHeightStyle = ''
	}: Props = $props();

	let isMultiline = $state(false);
	let messageElement: HTMLElement | undefined = $state();
	const currentConfig = $derived(config());
	const isRawTextMode = $derived(Boolean(config().showRawPlainText));

	let displayContent = $derived.by(() => {
		if (isRawTextMode) return content;
		let text = content.trim();
		if (text.startsWith('（') && text.endsWith('）')) {
			return text.substring(1, text.length - 1);
		}
		return content;
	});

	let formattedHtml = $derived.by(() => {
		const escapeHtml = (str: string) => {
			return str
				.replace(/&/g, '&amp;')
				.replace(/</g, '&lt;')
				.replace(/>/g, '&gt;')
				.replace(/"/g, '&quot;')
				.replace(/'/g, '&#039;');
		};
		const escaped = escapeHtml(displayContent);
		if (isRawTextMode) return escaped;
		// アスタリスクで囲まれたテキストを、アスタリスク非表示＋斜体＋グレーアウトのスパン要素に置換
		return escaped.replace(/\*([^*]+)\*/g, '<span class="italic text-muted-foreground opacity-80">$1</span>');
	});

	let isGrayOut = $derived.by(() => {
		if (isRawTextMode) return false;
		let text = content.trim();
		return text.startsWith('（') && text.endsWith('）');
	});

	let isHidden = $derived.by(() => {
		if (isRawTextMode) return false;
		let text = content.trim();
		return text.startsWith('「') && text.endsWith('」');
	});

	$effect(() => {
		if (!messageElement || !content.trim()) return;

		if (content.includes('\n')) {
			isMultiline = true;
			return;
		}

		const resizeObserver = new ResizeObserver((entries) => {
			for (const entry of entries) {
				const element = entry.target as HTMLElement;
				const estimatedSingleLineHeight = 24; // Typical line height for text-md

				isMultiline = element.offsetHeight > estimatedSingleLineHeight * 1.5;
			}
		});

		resizeObserver.observe(messageElement);

		return () => {
			resizeObserver.disconnect();
		};
	});
</script>

{#if attachments && attachments.length > 0}
	<div class="mb-2 max-w-[80%]">
		<ChatAttachmentsList {attachments} readonly imageHeight="h-40" />
	</div>
{/if}

{#if content.trim() && !isHidden}
	<Card
		class="chat-message-user-bubble max-w-[80%] overflow-y-auto rounded-[1.125rem] border-none bg-primary/5 px-3.75 py-1.5 {textColorClass} backdrop-blur-md data-multiline:py-2.5 {cardBgClass} {isGrayOut ? 'opacity-70' : ''}"
		data-multiline={isMultiline ? '' : undefined}
		style="{maxHeightStyle} overflow-wrap: anywhere; word-break: break-word;"
	>
		{#if renderMarkdown && currentConfig.renderUserContentAsMarkdown}
			<div bind:this={messageElement}>
				<MarkdownContent class="markdown-user-content -my-4" content={displayContent} />
			</div>
		{:else}
			<span bind:this={messageElement} class="text-sm md:text-md whitespace-pre-wrap">{@html formattedHtml}</span>
		{/if}
	</Card>
{/if}
