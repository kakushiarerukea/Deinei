<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Brain, Plus, Trash2, Loader2, Edit2, Check, X } from '@lucide/svelte';
	import { chatStore } from '$lib/stores/chat.svelte';
	import { conversationsStore } from '$lib/stores/conversations.svelte';
	import { t } from '$lib/stores/i18n.svelte';
	import { onMount } from 'svelte';

	interface Props {
		class?: string;
		onClose?: () => void;
	}

	let { class: className = '', onClose }: Props = $props();

	let memoryInput = $state('');
	let memories = $state<{ id: string; text: string; metadata?: { timestamp?: string } }[]>([]);
	let isLoading = $state(false);

	let editingId = $state<string | null>(null);
	let editingText = $state('');

	function formatTimestamp(isoStr?: string) {
		if (!isoStr) return '';
		try {
			const date = new Date(isoStr);
			if (isNaN(date.getTime())) {
				return isoStr.replace('T', ' ').split('.')[0];
			}
			const y = date.getFullYear();
			const m = String(date.getMonth() + 1).padStart(2, '0');
			const d = String(date.getDate()).padStart(2, '0');
			const hh = String(date.getHours()).padStart(2, '0');
			const mm = String(date.getMinutes()).padStart(2, '0');
			return `${y}/${m}/${d} ${hh}:${mm}`;
		} catch (e) {
			return isoStr.replace('T', ' ').split('.')[0];
		}
	}

	async function fetchMemories() {
		try {
			isLoading = true;
			const convId = conversationsStore.activeConversation?.id;
			const activeMsgIds = conversationsStore.activeMessages.map((m) => m.id).join(',');
			const url = convId
				? `http://localhost:5000/memory/list?session_id=${convId}&active_message_ids=${encodeURIComponent(activeMsgIds)}`
				: 'http://localhost:5000/memory/list';
			const res = await fetch(url);
			if (res.ok) {
				const data = await res.json();
				console.log('[RagPanel] Fetched memories count for active branch:', data.memories?.length);
				memories = [...(data.memories || [])];
			}
		} catch (e) {
			console.error('Failed to fetch memories', e);
		} finally {
			isLoading = false;
		}
	}

	$effect(() => {
		const convId = conversationsStore.activeConversation?.id;
		const currNode = conversationsStore.activeConversation?.currNode;
		const activeCount = conversationsStore.activeMessages.length;
		if (convId) {
			fetchMemories();
		}
	});

	onMount(() => {
		fetchMemories();
		const handleMemoryUpdate = () => fetchMemories();
		window.addEventListener('memoryUpdated', handleMemoryUpdate);
		return () => {
			window.removeEventListener('memoryUpdated', handleMemoryUpdate);
		};
	});

	async function handleAddMemory() {
		if (memoryInput.trim()) {
			const text = memoryInput.trim();
			memoryInput = '';
			isLoading = true;
			try {
				const convId = conversationsStore.activeConversation?.id;
				const currNode = conversationsStore.activeConversation?.currNode || '';
				const url = convId
					? `http://localhost:5000/memory/add?session_id=${convId}${currNode ? `&message_id=${currNode}` : ''}`
					: 'http://localhost:5000/memory/add';
				await fetch(url, {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ text, message_id: currNode })
				});
				await fetchMemories();
			} catch (e) {
				console.error('Failed to add memory', e);
				isLoading = false;
			}
		}
	}

	async function handleDeleteMemory(id: string) {
		try {
			isLoading = true;
			// 1. ローカル配列から即座に削除（UI楽観的更新）
			memories = memories.filter(m => m.id !== id);

			const convId = conversationsStore.activeConversation?.id;
			const url = convId
				? `http://localhost:5000/memory/${id}?session_id=${convId}`
				: `http://localhost:5000/memory/${id}`;

			const res = await fetch(url, {
				method: 'DELETE'
			});
			console.log(`[RagPanel] Delete HTTP response: ${res.status}`);
			await fetchMemories();
		} catch (e) {
			console.error('Failed to delete memory', e);
		} finally {
			isLoading = false;
		}
	}


	async function handleDeduplicate() {
		try {
			isLoading = true;
			const res = await fetch('http://localhost:5000/memory/deduplicate', { method: 'POST' });
			if (res.ok) {
				const data = await res.json();
				console.log(`✨ [RAG Deduplicate] 重複記憶の一括統合完了: ${data.merged} 件を統合削除、残存記憶: ${data.total_remaining} 件`);
				if (data.merged_details && data.merged_details.length > 0) {
					console.group('📋 [統合された記憶の詳細リスト]');
					data.merged_details.forEach((item: any, i: number) => {
						console.log(`${i + 1}. (類似度: ${item.similarity})`);
						console.log(`   ❌ 削除統合: 「${item.deleted}」`);
						console.log(`   ✅ 最新保持: 「${item.kept}」`);
					});
					console.groupEnd();
				} else {
					console.log('ℹ️ 統合対象となる重複記憶はありませんでした。');
				}
			}
			await fetchMemories();
		} catch (e) {
			console.error('Failed to deduplicate memories', e);
		} finally {
			isLoading = false;
		}
	}

	async function handleClearAllMemories() {
		if (!confirm('保存されているすべての記憶を完全に消去しますか？')) return;
		try {
			isLoading = true;
			memories = [];
			const convId = conversationsStore.activeConversation?.id;
			const url = convId
				? `http://localhost:5000/memory/clear?session_id=${convId}`
				: 'http://localhost:5000/memory/clear';
			await fetch(url, { method: 'POST' });
			await fetchMemories();
		} catch (e) {
			console.error('Failed to clear all memories', e);
		} finally {
			isLoading = false;
		}
	}

	function startEdit(id: string, text: string) {
		editingId = id;
		editingText = text;
	}

	function cancelEdit() {
		editingId = null;
		editingText = '';
	}

	async function handleUpdateMemory(id: string) {
		if (!editingText.trim()) return;
		try {
			isLoading = true;
			const convId = conversationsStore.activeConversation?.id;
			const url = convId
				? `http://localhost:5000/memory/update?session_id=${convId}`
				: 'http://localhost:5000/memory/update';
			const res = await fetch(url, {
				method: 'PUT',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ id, text: editingText.trim(), session_id: convId })
			});
			if (res.ok) {
				editingId = null;
				editingText = '';
				await fetchMemories();
			} else {
				console.error('Failed to update memory', res.statusText);
			}
		} catch (e) {
			console.error('Failed to update memory', e);
		} finally {
			isLoading = false;
		}
	}
</script>

<div class="flex flex-col h-full {className}">
	<div class="px-4 py-3 flex items-center justify-between border-b border-border">
		<h3 class="font-semibold text-sm flex items-center gap-2">
			<Brain class="w-4 h-4" /> RAG & Memory
		</h3>
		{#if onClose}
			<button class="text-muted-foreground hover:text-foreground p-1" onclick={onClose}>✕</button>
		{/if}
	</div>

	<div class="flex-1 overflow-y-auto p-4 flex flex-col gap-6">
		<!-- Add new memory -->
		<div class="flex flex-col gap-2">
			<h4 class="text-xs font-semibold uppercase text-muted-foreground">Add Knowledge</h4>
			<Textarea
				placeholder="Add a new fact to memory..."
				bind:value={memoryInput}
				class="resize-none text-sm min-h-[60px]"
			/>
			<Button
				size="sm"
				variant="secondary"
				onclick={handleAddMemory}
				class="w-full"
				disabled={isLoading}
			>
				{#if isLoading}
					<Loader2 class="w-4 h-4 mr-1 animate-spin" />
				{:else}
					<Plus class="w-4 h-4 mr-1" />
				{/if}
				Add Memory
			</Button>
		</div>


		<!-- Deduplicate Action -->
		<div class="flex flex-col gap-2">
			<Button
				size="sm"
				variant="secondary"
				onclick={handleDeduplicate}
				class="w-full text-xs"
				disabled={isLoading}
			>
				{#if isLoading}
					<Loader2 class="w-4 h-4 mr-1.5 animate-spin" />
				{:else}
					<Brain class="w-4 h-4 mr-1.5" />
				{/if}
				{t('rag.deduplicate', 'Deduplicate Memories')}
			</Button>
		</div>

		<!-- Reasoning Logs -->
		{#if chatStore.lastSummaryReasoning || chatStore.lastFactReasoning || chatStore.isSummarizing}
			<div class="flex flex-col gap-2 border border-border/80 rounded-md p-2 bg-muted/20">
				<h4 class="text-xs font-semibold uppercase text-muted-foreground flex items-center gap-1.5">
					<Brain class="w-3.5 h-3.5" /> {t('rag.reasoningLogs', 'Reasoning Logs')}
				</h4>
				<div class="flex flex-col gap-1 text-[11px] text-muted-foreground max-h-[220px] overflow-y-auto leading-relaxed">
					{#if chatStore.isSummarizing}
						<div class="flex items-center gap-1.5 py-1 text-accent-foreground/80 italic">
							<Loader2 class="w-3 h-3 animate-spin" />
							{t('rag.deduplicating', 'Deduplicating...')}
						</div>
					{/if}
					{#if chatStore.lastSummaryReasoning}
						<details class="group border-b border-border/50 pb-1">
							<summary class="cursor-pointer font-medium hover:text-foreground py-1 flex justify-between items-center select-none">
								<span>{t('rag.summaryReasoning', '1. Summary Reasoning')}</span>
								<span class="text-[9px] px-1 bg-muted rounded border border-border group-open:hidden">{t('rag.open', 'Open')}</span>
								<span class="text-[9px] px-1 bg-muted rounded border border-border hidden group-open:inline">{t('rag.close', 'Close')}</span>
							</summary>
							<div class="pl-2 pt-1 pb-1.5 border-l border-border text-[10px] whitespace-pre-wrap font-mono break-all opacity-85 select-text selection:bg-primary/20">
								{chatStore.lastSummaryReasoning}
							</div>
						</details>
					{/if}
					{#if chatStore.lastSummaryOutput}
						<details class="group border-b border-border/50 pb-1">
							<summary class="cursor-pointer font-medium hover:text-foreground py-1 flex justify-between items-center select-none">
								<span>{t('rag.summaryOutput', '1-2. Summary Final Output')}</span>
								<span class="text-[9px] px-1 bg-muted rounded border border-border group-open:hidden">{t('rag.open', 'Open')}</span>
								<span class="text-[9px] px-1 bg-muted rounded border border-border hidden group-open:inline">{t('rag.close', 'Close')}</span>
							</summary>
							<div class="pl-2 pt-1 pb-1.5 border-l border-primary/40 text-[10px] whitespace-pre-wrap break-all opacity-95 text-foreground select-text selection:bg-primary/20 bg-muted/30 p-1.5 rounded">
								{chatStore.lastSummaryOutput}
							</div>
						</details>
					{/if}
					{#if chatStore.lastFactReasoning}
						<details class="group border-b border-border/50 pb-1">
							<summary class="cursor-pointer font-medium hover:text-foreground py-1 flex justify-between items-center select-none">
								<span>{t('rag.factReasoning', '2. Fact Extraction Reasoning')}</span>
								<span class="text-[9px] px-1 bg-muted rounded border border-border group-open:hidden">{t('rag.open', 'Open')}</span>
								<span class="text-[9px] px-1 bg-muted rounded border border-border hidden group-open:inline">{t('rag.close', 'Close')}</span>
							</summary>
							<div class="pl-2 pt-1 pb-1.5 border-l border-border text-[10px] whitespace-pre-wrap font-mono break-all opacity-85 select-text selection:bg-primary/20">
								{chatStore.lastFactReasoning}
							</div>
						</details>
					{/if}
					{#if chatStore.lastFactOutput}
						<details class="group">
							<summary class="cursor-pointer font-medium hover:text-foreground py-1 flex justify-between items-center select-none">
								<span>{t('rag.factOutput', '2-2. Fact Extraction Final Output')}</span>
								<span class="text-[9px] px-1 bg-muted rounded border border-border group-open:hidden">{t('rag.open', 'Open')}</span>
								<span class="text-[9px] px-1 bg-muted rounded border border-border hidden group-open:inline">{t('rag.close', 'Close')}</span>
							</summary>
							<div class="pl-2 pt-1 pb-1.5 border-l border-primary/40 text-[10px] whitespace-pre-wrap break-all opacity-95 text-foreground select-text selection:bg-primary/20 bg-muted/30 p-1.5 rounded">
								{chatStore.lastFactOutput}
							</div>
						</details>
					{/if}
				</div>
			</div>
		{/if}

		<div class="flex flex-col gap-2">
			<div class="flex items-center justify-between">
				<h4 class="text-xs font-semibold uppercase text-muted-foreground">Stored Memories ({memories.length})</h4>
				<div class="flex items-center gap-2">
					{#if memories.length > 0}
						<button
							class="text-[10px] text-destructive hover:underline opacity-80 hover:opacity-100"
							onclick={handleClearAllMemories}
							disabled={isLoading}
						>
							Clear All
						</button>
					{/if}
					{#if isLoading}
						<Loader2 class="w-3.5 h-3.5 animate-spin text-muted-foreground" />
					{/if}
				</div>
			</div>
			<div class="flex flex-col gap-2">
				{#each memories as memory (memory.id)}
					<div
						class="p-2 rounded-md bg-muted/50 border border-border text-xs leading-relaxed flex justify-between items-start gap-2 group min-h-[38px]"
					>
						{#if editingId === memory.id}
							<div class="flex-1 flex items-center gap-1.5 w-full">
								<input
									type="text"
									bind:value={editingText}
									class="flex-1 bg-background border border-input rounded px-1.5 py-0.5 text-xs text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
									disabled={isLoading}
									onkeydown={(e) => {
										if (e.key === 'Enter') handleUpdateMemory(memory.id);
										if (e.key === 'Escape') cancelEdit();
									}}
								/>
								<button
									class="text-muted-foreground hover:text-foreground p-0.5 rounded-md flex-shrink-0"
									onclick={() => handleUpdateMemory(memory.id)}
									title="Save"
									disabled={isLoading}
								>
									<Check class="w-3.5 h-3.5" />
								</button>
								<button
									class="text-muted-foreground hover:text-foreground p-0.5 rounded-md flex-shrink-0"
									onclick={cancelEdit}
									title="Cancel"
									disabled={isLoading}
								>
									<X class="w-3.5 h-3.5" />
								</button>
							</div>
						{:else}
							<div class="flex-1 overflow-hidden break-words py-0.5">
								{#if memory.metadata && memory.metadata.type === 'parent_child'}
									<div class="flex items-center gap-1.5 mb-1">
										<span class="text-[9px] font-semibold bg-primary/20 text-primary border border-primary/30 px-1.5 py-0.2 rounded">
											{t('rag.episodes', 'Recalled Episodes')}
										</span>
									</div>
								{/if}
								<div class="text-foreground/90 font-medium">{memory.text}</div>
								{#if memory.metadata && memory.metadata.parent_text}
									<details class="mt-1.5 group/parent">
										<summary class="cursor-pointer text-[10px] text-muted-foreground/80 hover:text-foreground flex items-center gap-1 select-none">
											<span>📖 {t('rag.viewEpisode', 'View Full Episode')}</span>
										</summary>
										<div class="mt-1 p-2 bg-black/30 border border-border/50 rounded text-[11px] text-muted-foreground whitespace-pre-wrap leading-relaxed">
											{memory.metadata.parent_text}
										</div>
									</details>
								{/if}
								{#if memory.metadata && memory.metadata.timestamp}
									<div class="text-[10px] text-muted-foreground/60 mt-1 select-none">
										{formatTimestamp(memory.metadata.timestamp)}
									</div>
								{/if}
							</div>
							<div class="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
								<button
									class="text-muted-foreground hover:text-foreground p-1 rounded-md"
									onclick={() => startEdit(memory.id, memory.text)}
									title="Edit memory"
									disabled={isLoading}
								>
									<Edit2 class="w-3.5 h-3.5" />
								</button>
								<button
									class="text-muted-foreground hover:text-destructive p-1 rounded-md"
									onclick={() => handleDeleteMemory(memory.id)}
									title="Delete memory"
									disabled={isLoading}
								>
									<Trash2 class="w-3.5 h-3.5" />
								</button>
							</div>
						{/if}
					</div>
				{/each}
				{#if memories.length === 0 && !isLoading}
					<div class="text-xs text-muted-foreground italic text-center p-4">
						No memories stored.
					</div>
				{/if}
			</div>
		</div>
	</div>
</div>
