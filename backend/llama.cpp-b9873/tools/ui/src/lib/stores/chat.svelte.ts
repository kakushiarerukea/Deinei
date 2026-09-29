/**
 * chatStore - Reactive State Store for Chat Operations
 *
 * Manages chat lifecycle, streaming, message operations, and processing state.
 *
 * **Architecture & Relationships:**
 * - **ChatService**: Stateless API layer (sendMessage, streaming)
 * - **chatStore** (this): Reactive state + business logic
 * - **conversationsStore**: Conversation persistence and navigation
 *
 * @see ChatService in services/chat.service.ts for API operations
 */

import { SvelteMap, SvelteSet } from 'svelte/reactivity';
import { DatabaseService } from '$lib/services/database.service';
import { ChatService } from '$lib/services/chat.service';
import { streamIdentity } from '$lib/utils/stream-identity';
import { getAuthHeaders } from '$lib/utils/api-headers';
import { conversationsStore } from '$lib/stores/conversations.svelte';
import { config } from '$lib/stores/settings.svelte';
import { agenticStore } from '$lib/stores/agentic.svelte';
import { mcpStore } from '$lib/stores/mcp.svelte';
import { contextSize, isRouterMode } from '$lib/stores/server.svelte';
import { ttsStore } from '$lib/stores/tts.svelte';
import { t } from '$lib/stores/i18n.svelte';
import {
	selectedModelName,
	modelsStore,
	selectedModelContextSize
} from '$lib/stores/models.svelte';
import {
	normalizeModelName,
	filterByLeafNodeId,
	findDescendantMessages,
	findLeafNode,
	findMessageById,
	isAbortError,
	generateConversationTitle
} from '$lib/utils';
import { classifyContinueIntent } from '$lib/utils/agentic';
import {
	MAX_INACTIVE_CONVERSATION_STATES,
	INACTIVE_CONVERSATION_STATE_MAX_AGE_MS,
	SYSTEM_MESSAGE_PLACEHOLDER,
	TITLE_GENERATION,
	API_CHAT
} from '$lib/constants';
import { DEFAULT_SYSTEM_PROMPT } from '$lib/constants/default-system-prompt';
import type {
	ChatMessageTimings,
	ChatMessagePromptProgress,
	ChatStreamCallbacks,
	ErrorDialogState
} from '$lib/types/chat';
import type {
	ApiChatMessageData,
	ApiProcessingState,
	ApiStreamSession,
	DatabaseMessage,
	DatabaseMessageExtra
} from '$lib/types';
import type { ArchivedSummaryEntry } from '$lib/types/database';
import {
	ContinueIntentKind,
	ErrorDialogType,
	MessageRole,
	MessageType,
	StreamConnectionState,
	ContentPartType
} from '$lib/enums';
import { toast } from 'svelte-sonner';

interface ConversationStateEntry {
	lastAccessed: number;
}

export const MAX_SUMMARY_BLOCKS = 16;
export const FORGET_BLOCKS_COUNT = 2;

export const DEFAULT_SUMMARY_SYSTEM_PROMPT = `You are an objective summarization engine. Roleplaying, continuing the conversation, or speaking as a persona is strictly prohibited.
Based on the previous background context, objectively summarize only the events of the newly provided conversation log.

[Rules]
1. Summarize only the actual events, user statements, and AI responses that occurred in the new conversation log.
2. Highlight 2 to 3 essential keywords or proper nouns using quotes/brackets (e.g., "Google" or 「Google」) for future recall and retrieval. Do not overuse quotes.
3. Keep the summary concise (approx. 300-500 tokens / 1000 characters).
4. Output ONLY the summary text. Do not include greetings, introductions, or explanatory notes.
5. IMPORTANT: Write the summary in the primary language used in the conversation log (e.g., Japanese if the conversation was in Japanese, English if in English).`;

export const DEFAULT_FACT_SYSTEM_PROMPT = `You are a factual memory extraction engine. Extract personal, concrete facts (preferences, experiences, daily events, relationships, schedules, promises, etc.) from the conversation log.

[Rules]
1. Output one fact per line, beginning with "- ".
2. Never omit the subject. Always identify who the fact belongs to:
   - Refer to the user as "User" (or 「ユーザー」 in Japanese).
   - Refer to the assistant/AI as "Self" (or 「自分」 in Japanese).
   Example:
   - User likes sushi. (or - ユーザーは寿司が好きである)
   - Self promised User to go to the park tomorrow. (or - 自分とユーザーは、明日公園へ行くと約束した。)
3. Exclude overly generic interactions (e.g., greetings, brief small talk). Extract only personal, specific facts worth long-term memory.
4. If no meaningful new facts are found, output ONLY "None" (or "なし" if in Japanese). Do not provide any explanation.
5. IMPORTANT: Write the extracted facts in the primary language used in the conversation log (e.g., Japanese if the conversation was in Japanese, English if in English).`;

export const DEFAULT_CHILD_EXTRACT_SYSTEM_PROMPT = `You are an episode indexing engine. Given a narrative summary of a past episode, extract 2 to 4 key search phrases (specific events, places, actions, topics, proper nouns) as bullet points for future memory retrieval.

[Rules]
1. Start each line with "- ".
2. Include concrete proper nouns, places, actions, or topics discussed.
3. Example output:
   - Read an astrology book together at the library
   - Ate apple pie at a cafe on the way home
4. Do not include any greetings, prefaces, or explanations.
5. IMPORTANT: Output in the primary language used in the provided episode summary (e.g., Japanese if in Japanese, English if in English).`;

export function getSummaryConfig() {
	if (typeof localStorage === 'undefined') {
		return {
			summaryPrompt: DEFAULT_SUMMARY_SYSTEM_PROMPT,
			summaryTemp: 0.4,
			summaryMaxTokens: 4096,
			factPrompt: DEFAULT_FACT_SYSTEM_PROMPT,
			factTemp: 0.2,
			factMaxTokens: 4096
		};
	}

	let savedSummaryPrompt = localStorage.getItem('deining_summary_prompt');
	let savedFactPrompt = localStorage.getItem('deining_fact_prompt');
	// 古い日本語デフォルトプロンプトが残っている場合は最新プロンプトに自動アップグレード
	if (savedSummaryPrompt && (
		savedSummaryPrompt.includes('あなたは要約マシンです') ||
		savedSummaryPrompt.includes('ユーザーが送った具体的な単語・キーワード・固有名詞は維持すること') ||
		!savedSummaryPrompt.includes('objective summarization engine')
	)) {
		savedSummaryPrompt = DEFAULT_SUMMARY_SYSTEM_PROMPT;
		localStorage.setItem('deining_summary_prompt', DEFAULT_SUMMARY_SYSTEM_PROMPT);
	}

	if (savedFactPrompt && (
		savedFactPrompt.includes('あなたは事実抽出マシンです') ||
		!savedFactPrompt.includes('factual memory extraction engine')
	)) {
		savedFactPrompt = DEFAULT_FACT_SYSTEM_PROMPT;
		localStorage.setItem('deining_fact_prompt', DEFAULT_FACT_SYSTEM_PROMPT);
	}

	return {
		summaryPrompt: savedSummaryPrompt || DEFAULT_SUMMARY_SYSTEM_PROMPT,
		summaryTemp: parseFloat(localStorage.getItem('deining_summary_temp') || '0.4'),
		summaryMaxTokens: parseInt(localStorage.getItem('deining_summary_max_tokens') || '4096', 10),
		factPrompt: savedFactPrompt || DEFAULT_FACT_SYSTEM_PROMPT,
		factTemp: parseFloat(localStorage.getItem('deining_fact_temp') || '0.2'),
		factMaxTokens: parseInt(localStorage.getItem('deining_fact_max_tokens') || '4096', 10)
	};
}

class ChatStore {
	activeProcessingState = $state<ApiProcessingState | null>(null);
	isSummarizing = $state(false);
	rebuildingConversationId = $state<string | null>(null);
	summarizeStatusText = $state('');
	lastSummaryReasoning = $state('');
	lastFactReasoning = $state('');
	lastSummaryOutput = $state('');
	lastFactOutput = $state('');
	currentResponse = $state('');
	errorDialogState = $state<ErrorDialogState | null>(null);
	isLoading = $state(false);
	// true while the active conversation streams reasoning content but no visible content yet
	isReasoning = $state(false);
	// resumable stream connection state for the active conversation
	// streaming -> bytes flowing normally, resuming -> waiting on /v1/stream/:id reconnect, lost -> unrecoverable
	streamConnectionState = $state<StreamConnectionState>(StreamConnectionState.STREAMING);
	chatLoadingStates = new SvelteMap<string, boolean>();
	chatReasoningStates = new SvelteMap<string, boolean>();
	chatStreamingStates = new SvelteMap<
		string,
		{ response: string; messageId: string; model?: string | null }
	>();
	// convs that the backend reports as having a running session, populated by the global sync
	// at app mount and on visibilitychange. it does not overlap with chatLoadingStates which
	// tracks inferences driven by this browser, both are unioned to feed the sidebar spinners
	private remoteRunningConvs = new SvelteSet<string>();
	// per conv attach lifecycle, used to derive the global streaming flag without flipping it
	// off when one conv finishes while another is still streaming. mirrors chatLoadingStates
	// in scope but tracks the attach + tee replay path specifically
	private attachingConvs = new SvelteSet<string>();
	// in-flight discoverActiveStream guard, keyed by conv id
	private discoveringConvs = new SvelteSet<string>();
	private abortControllers = new SvelteMap<string, AbortController>();
	private preEncodeAbortController: AbortController | null = null;
	private processingStates = new SvelteMap<string, ApiProcessingState | null>();
	private conversationStateTimestamps = new SvelteMap<string, ConversationStateEntry>();
	private summaryFailuresByConv = new SvelteMap<string, number>();
	private activeConversationId = $state<string | null>(null);
	private isStreamingActive = $state(false);
	private isEditModeActive = $state(false);
	private addFilesHandler: ((files: File[]) => void) | null = $state(null);
	pendingEditMessageId = $state<string | null>(null);
	private messageUpdateCallback:
		| ((messageId: string, updates: Partial<DatabaseMessage>) => void)
		| null = null;
	private _pendingDraftMessage = $state<string>('');
	private _pendingDraftFiles = $state<ChatUploadedFile[]>([]);

	/** Reactive: queued pending messages for non-agentic streaming */
	private _pendingMessages = new SvelteMap<
		string,
		{ content: string; extras?: DatabaseMessageExtra[] }
	>();

	private setChatLoading(convId: string, loading: boolean): void {
		this.touchConversationState(convId);
		if (loading) {
			this.chatLoadingStates.set(convId, true);
			if (convId === conversationsStore.activeConversation?.id) this.isLoading = true;
		} else {
			this.chatLoadingStates.delete(convId);
			if (convId === conversationsStore.activeConversation?.id) this.isLoading = false;
			this.setChatReasoning(convId, false);
			// the local pipe is the authoritative observer of session end: when it finishes (clean
			// onComplete or explicit Stop), the backend session is finalized too, so we drop the
			// sidebar hint for this conv right away instead of waiting for the next visibilitychange
			// snapshot. without this the spinner ghosts until the user toggles the tab
			this.remoteRunningConvs.delete(convId);
		}
	}

	private setChatReasoning(convId: string, reasoning: boolean): void {
		if (reasoning) {
			this.chatReasoningStates.set(convId, true);
			if (convId === conversationsStore.activeConversation?.id) this.isReasoning = true;
		} else {
			this.chatReasoningStates.delete(convId);
			if (convId === conversationsStore.activeConversation?.id) this.isReasoning = false;
		}
	}
	private setChatStreaming(
		convId: string,
		response: string,
		messageId: string,
		model?: string | null
	): void {
		this.touchConversationState(convId);
		this.chatStreamingStates.set(convId, {
			response,
			messageId,
			model: model ?? this.chatStreamingStates.get(convId)?.model
		});
		if (convId === conversationsStore.activeConversation?.id) this.currentResponse = response;
	}
	private clearChatStreaming(convId: string, messageId?: string): void {
		// session aware: a stale generation must not wipe a newer one's streaming state on the
		// same conversation, that would drop the frozen stop identity and stop the wrong session
		if (messageId !== undefined) {
			const cur = this.chatStreamingStates.get(convId);
			if (cur && cur.messageId !== messageId) return;
		}
		this.chatStreamingStates.delete(convId);
		if (convId === conversationsStore.activeConversation?.id) this.currentResponse = '';
	}
	private getChatStreaming(convId: string): { response: string; messageId: string } | undefined {
		return this.chatStreamingStates.get(convId);
	}
	syncLoadingStateForChat(convId: string): void {
		this.isLoading = this.chatLoadingStates.get(convId) || false;
		this.isReasoning = this.chatReasoningStates.get(convId) || false;
		const s = this.chatStreamingStates.get(convId);
		this.currentResponse = s?.response || '';
		this.isStreamingActive = s !== undefined;
		this.setActiveProcessingConversation(convId);
		// Sync streaming content to activeMessages so UI displays current content
		if (s?.response && s?.messageId) {
			const idx = conversationsStore.findMessageIndex(s.messageId);
			if (idx !== -1) {
				conversationsStore.updateMessageAtIndex(idx, { content: s.response });
			}
		}
	}
	/**
	 * Server side stream discovery, split in three pieces:
	 *
	 * probeServerStream(convId) -> hits POST /v1/streams/lookup with the conv id, returns the session to attach
	 *   to or null. Pure read, no side effect, no UI lock. Safe to fire in parallel with anything.
	 *
	 * attachServerStream(convId) -> flips the spinner immediately, fetches the replay stream
	 *   from byte 0, finds the assistant slot to splice into (creates a placeholder if the conv has
	 *   no assistant message yet, for cross device or fresh local DB cases), and pipes the SSE bytes
	 *   into the message via handleStreamResponse.
	 *
	 * discoverActiveStream(convId) -> probe + attach in one call. Used by callers that do not need
	 *   to overlap the probe with other async work.
	 *
	 * The mount of the chat page in +page.svelte calls probeServerStream in parallel with
	 * loadConversation, then attachServerStream once both have settled. This gives the earliest
	 * possible time to spinner and avoids racing against an empty activeMessages array.
	 */
	async probeServerStream(convId: string): Promise<ApiStreamSession | null> {
		if (!convId) return null;
		let listResp: Response;
		try {
			// POST the one conv id we are probing
			listResp = await fetch(`./v1/streams/lookup`, {
				method: 'POST',
				headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
				body: JSON.stringify({ conversation_ids: [convId] })
			});
		} catch (e) {
			console.warn('probeServerStream fetch failed:', e);
			return null;
		}
		if (!listResp.ok) {
			console.warn(`probeServerStream got HTTP ${listResp.status} for conv ${convId}`);
			return null;
		}
		let sessions: ApiStreamSession[];
		try {
			sessions = (await listResp.json()) as ApiStreamSession[];
		} catch (e) {
			console.warn('probeServerStream JSON parse failed:', e);
			return null;
		}
		return ChatService.selectActiveStream(sessions);
	}

	async attachServerStream(convId: string, streamId?: string): Promise<void> {
		if (!convId) return;
		if (this.chatStreamingStates.has(convId)) return;

		// flip the spinner immediately, the user sees activity as soon as the conv becomes active.
		// the global isStreamingActive flag is derived from attachingConvs.size, so adding here
		// turns it on, and removing in unlock only turns it off when this is the last attach
		this.setChatLoading(convId, true);
		this.attachingConvs.add(convId);
		this.setStreamingActive(true);
		// only set the active processing conv if we are looking at it, otherwise a background
		// attach would steal the indicator from the conv the user is currently viewing
		if (convId === conversationsStore.activeConversation?.id) {
			this.setActiveProcessingConversation(convId);
		}

		const unlock = () => {
			this.attachingConvs.delete(convId);
			// flip the global flag off only when no other conv is still attaching
			if (this.attachingConvs.size === 0) {
				this.setStreamingActive(false);
			}
			this.setChatLoading(convId, false);
			this.clearChatStreaming(convId);
		};

		// fetch the replay stream from byte 0, rebuild the assistant message from scratch.
		// resolve the server side identity, fall back to streamIdentity when the caller does not
		// pass a streamId. probeServerStream returns the full id (with ::model suffix when present)
		const id = streamId || streamIdentity(convId, selectedModelName());
		let response: Response;
		try {
			response = await fetch(`./v1/stream/${encodeURIComponent(id)}?from=0`, {
				headers: getAuthHeaders()
			});
		} catch (e) {
			console.error('attachServerStream replay fetch failed:', e);
			unlock();
			return;
		}
		if (!response.ok) {
			console.warn(`attachServerStream replay got HTTP ${response.status} for conv ${convId}`);
			unlock();
			return;
		}

		// load the target conversation messages by id, not via the active store. when multiple
		// attaches run in parallel the active store may reflect another conv and writing through
		// its index mixes content across convs (CoT flicker, message bleed). by going through the
		// DB we stay isolated, and only mirror into the active store when the attached conv is
		// the one currently displayed
		let messages: DatabaseMessage[];
		try {
			messages = await DatabaseService.getConversationMessages(convId);
		} catch (e) {
			console.error('attachServerStream load messages failed:', e);
			unlock();
			return;
		}

		// locate the slot to splice into, create a placeholder assistant message if there is none.
		// we use the conv-scoped findLastAssistantIdx helpers, they only depend on the array
		let targetIdx = this.findLastAssistantIdx(messages);
		if (targetIdx === -1) {
			const lastUserIdx = this.findLastUserIdx(messages);
			if (lastUserIdx === -1) {
				console.warn(
					`attachServerStream: conv ${convId} has no user or assistant message, cannot splice`
				);
				unlock();
				return;
			}
			try {
				const placeholder = await DatabaseService.createMessageBranch(
					{
						convId,
						role: MessageRole.ASSISTANT,
						content: '',
						type: MessageType.TEXT,
						timestamp: Date.now(),
						parent: messages[lastUserIdx].id,
						children: [],
						toolCalls: ''
					} as Omit<DatabaseMessage, 'id'>,
					messages[lastUserIdx].id
				);
				messages = [...messages, placeholder];
				targetIdx = messages.length - 1;
				// only push into the active store when this conv is the one displayed right now
				if (convId === conversationsStore.activeConversation?.id) {
					conversationsStore.addMessageToActive(placeholder);
				}
			} catch (e) {
				console.error('attachServerStream placeholder creation failed:', e);
				unlock();
				return;
			}
		}
		if (targetIdx === -1) {
			unlock();
			return;
		}
		const targetMessage = messages[targetIdx];
		const targetMessageId = targetMessage.id;
		// when the assistant slot already has content, the running session is a continue or
		// another append flow and its buffer holds only the appended deltas. preserve the prefix
		// and let the replay add to it. when the slot is empty the session buffer holds the whole
		// message so we wipe and rebuild from byte 0
		const existingContent = targetMessage.content ?? '';
		const existingReasoning = targetMessage.reasoningContent ?? '';
		const isAppendMode = existingContent.length > 0;

		// helper: write to the active store only when the attached conv is currently displayed.
		// the lookup by message id is robust to reordering of activeMessages, two parallel attaches
		// can no longer step on each other's indices
		const writeActive = (updates: Partial<DatabaseMessage>) => {
			if (convId !== conversationsStore.activeConversation?.id) {
				return;
			}
			const liveIdx = conversationsStore.findMessageIndex(targetMessageId);
			if (liveIdx === -1) return;
			conversationsStore.updateMessageAtIndex(liveIdx, updates);
		};

		if (!isAppendMode) {
			writeActive({ content: '', reasoningContent: undefined });
		}

		// extract the model suffix, the resume calls in handleStreamResponse must reuse the model
		// the session was tagged with, not the live dropdown
		const sepIdx = id.indexOf('::');
		const attachedModel: string | null = sepIdx === -1 ? null : id.slice(sepIdx + 2);
		this.setChatStreaming(convId, existingContent, targetMessageId, attachedModel);
		const abortController = this.getOrCreateAbortController(convId);

		let streamedContent = '';
		let streamedReasoningContent = '';

		const cleanup = () => {
			unlock();
			this.setProcessingState(convId, null);
		};

		try {
			await ChatService.handleStreamResponse(
				response,
				(chunk: string) => {
					streamedContent += chunk;
					const displayed = isAppendMode ? existingContent + streamedContent : streamedContent;
					writeActive({ content: displayed });
					this.setChatStreaming(convId, displayed, targetMessageId);
				},
				async (
					finalContent?: string,
					reasoningContent?: string,
					timings?: ChatMessageTimings,
					toolCalls?: string
				) => {
					const streamed = streamedContent || finalContent || '';
					const streamedR = streamedReasoningContent || reasoningContent || '';
					const content = isAppendMode ? existingContent + streamed : streamed;
					const reasoning = isAppendMode ? existingReasoning + streamedR : streamedR;
					// the DB write is the source of truth, mirror to the active store only when
					// the conv is currently displayed
					await DatabaseService.updateMessage(targetMessageId, {
						content,
						reasoningContent: reasoning || undefined,
						toolCalls: toolCalls || '',
						timings
					});
					writeActive({
						content,
						reasoningContent: reasoning || undefined,
						timings
					});
					cleanup();
				},
				(err: Error) => {
					console.error('attachServerStream pipe error:', err);
					cleanup();
				},
				(chunk: string) => {
					streamedReasoningContent += chunk;
					const displayed = isAppendMode
						? existingReasoning + streamedReasoningContent
						: streamedReasoningContent;
					writeActive({ reasoningContent: displayed });
				},
				undefined,
				undefined,
				undefined,
				undefined,
				convId,
				abortController.signal,
				(connState: StreamConnectionState) => {
					if (convId === conversationsStore.activeConversation?.id) {
						this.streamConnectionState = connState;
					}
				},
				attachedModel
			);
		} catch (e) {
			console.error('attachServerStream pipe crashed:', e);
			cleanup();
		}
	}

	async discoverActiveStream(convId: string): Promise<void> {
		if (!convId) return;
		if (this.chatStreamingStates.has(convId)) return;
		if (this.chatLoadingStates.get(convId)) return;
		// concurrency guard: another discover may already be running for this conv (typical race
		// between mount and visibilitychange on tab switch). a second concurrent fetch on the same
		// /v1/stream/<id> would duplicate every byte into the DB message, this guard bounces it
		if (this.discoveringConvs.has(convId)) return;
		this.discoveringConvs.add(convId);

		try {
			// the model is frozen at POST time, rebuild the exact conv::model identity from the
			// persisted state so the lookup key matches what the server stored. null means a single
			// model conv with no ::suffix, only guess from the dropdown with no persisted state
			const localState = ChatService.getStreamState(convId);
			const streamId = ChatService.resumeStreamIdentity(convId, localState, selectedModelName());

			// primary path: ask the server which sessions exist for this identity
			const serverTarget = await this.probeServerStream(streamId);
			if (serverTarget) {
				// pass the full server side identity (may carry a ::model suffix) so the GET routes
				// straight to the owning session, no probe or fan out
				await this.attachServerStream(convId, serverTarget.conversation_id);
				return;
			}

			// fallback: local state remembers an interrupted byte offset for this conv, the server may
			// still have a live session matching that identity (we just lost the bytes mid stream). retry
			// with the frozen identity, the server probe inside attachServerStream tells us if it exists
			if (!localState) {
				return;
			}
			await this.attachServerStream(convId, streamId);
			// if attachServerStream failed (session gone, TTL expired), clear the local state to avoid retrying forever
			if (!this.chatStreamingStates.has(convId) && !this.chatLoadingStates.get(convId)) {
				ChatService.clearStreamState(convId);
			}
		} finally {
			this.discoveringConvs.delete(convId);
		}
	}

	private findLastAssistantIdx(messages: DatabaseMessage[]): number {
		for (let i = messages.length - 1; i >= 0; i--) {
			if (messages[i].role === MessageRole.ASSISTANT) return i;
		}
		return -1;
	}

	private findLastUserIdx(messages: DatabaseMessage[]): number {
		for (let i = messages.length - 1; i >= 0; i--) {
			if (messages[i].role === MessageRole.USER) return i;
		}
		return -1;
	}

	clearUIState(): void {
		this.isLoading = false;
		this.currentResponse = '';
		this.isStreamingActive = false;
	}

	setActiveProcessingConversation(conversationId: string | null): void {
		this.activeConversationId = conversationId;
		this.activeProcessingState = conversationId
			? this.processingStates.get(conversationId) || null
			: null;
	}

	getProcessingState(conversationId: string): ApiProcessingState | null {
		return this.processingStates.get(conversationId) || null;
	}

	private setProcessingState(conversationId: string, state: ApiProcessingState | null): void {
		if (state === null) this.processingStates.delete(conversationId);
		else this.processingStates.set(conversationId, state);
		if (conversationId === this.activeConversationId) this.activeProcessingState = state;
	}

	clearProcessingState(conversationId: string): void {
		this.processingStates.delete(conversationId);
		if (conversationId === this.activeConversationId) this.activeProcessingState = null;
	}

	getActiveProcessingState(): ApiProcessingState | null {
		return this.activeProcessingState;
	}

	getCurrentProcessingStateSync(): ApiProcessingState | null {
		return this.activeProcessingState;
	}

	private setStreamingActive(active: boolean): void {
		this.isStreamingActive = active;
	}

	isStreaming(): boolean {
		return this.isStreamingActive;
	}

	private getOrCreateAbortController(convId: string): AbortController {
		let c = this.abortControllers.get(convId);
		if (!c || c.signal.aborted) {
			c = new AbortController();
			this.abortControllers.set(convId, c);
		}
		return c;
	}

	private abortRequest(convId?: string): void {
		if (convId) {
			const c = this.abortControllers.get(convId);
			if (c) {
				c.abort();
				this.abortControllers.delete(convId);
			}
		} else {
			for (const c of this.abortControllers.values()) c.abort();
			this.abortControllers.clear();
		}
	}

	/**
	 * Abort the current agentic flow signal without clearing loading state.
	 * Used by "Send immediately" to force the agentic loop to exit so that
	 * the pending steering message can be re-sent.
	 */
	abortCurrentFlow(convId: string): void {
		const c = this.abortControllers.get(convId);
		if (c) {
			c.abort();
			this.abortControllers.delete(convId);
		}
	}

	private showErrorDialog(state: ErrorDialogState | null): void {
		this.errorDialogState = state;
	}

	dismissErrorDialog(): void {
		this.errorDialogState = null;
	}

	clearEditMode(): void {
		this.isEditModeActive = false;
		this.addFilesHandler = null;
	}

	isEditing(): boolean {
		return this.isEditModeActive;
	}

	setEditModeActive(handler: (files: File[]) => void): void {
		this.isEditModeActive = true;
		this.addFilesHandler = handler;
	}

	getAddFilesHandler(): ((files: File[]) => void) | null {
		return this.addFilesHandler;
	}

	clearPendingEditMessageId(): void {
		this.pendingEditMessageId = null;
	}

	savePendingDraft(message: string, files: ChatUploadedFile[]): void {
		this._pendingDraftMessage = message;
		this._pendingDraftFiles = [...files];
	}

	consumePendingDraft(): { message: string; files: ChatUploadedFile[] } | null {
		if (!this._pendingDraftMessage && this._pendingDraftFiles.length === 0) return null;
		const d = { message: this._pendingDraftMessage, files: [...this._pendingDraftFiles] };
		this._pendingDraftMessage = '';
		this._pendingDraftFiles = [];
		return d;
	}

	hasPendingDraft(): boolean {
		return Boolean(this._pendingDraftMessage) || this._pendingDraftFiles.length > 0;
	}

	getAllLoadingChats(): string[] {
		// union of local (this browser is piping) and remote (backend reports a running session
		// for this conv but no local pipe yet) sources. the sidebar shows one spinner per entry
		const out = new SvelteSet<string>(this.chatLoadingStates.keys());
		for (const id of this.remoteRunningConvs) {
			out.add(id);
		}
		return Array.from(out);
	}

	getAllStreamingChats(): string[] {
		return Array.from(this.chatStreamingStates.keys());
	}

	/**
	 * Resync the remote running convs set from the backend. Called by the layout at mount and on
	 * visibilitychange, no polling. A snapshot semantic: the set is replaced wholesale, stale entries
	 * for sessions that finalized while the browser was elsewhere are dropped naturally.
	 */
	async syncRemoteRunningStreams(): Promise<void> {
		// the conversations store loads from IndexedDB asynchronously, the +layout onMount caller
		// fires before that finishes. read ids straight from the DB so the result does not depend
		// on the store init race, and the sidebar spinners light up at first paint for every conv
		// the user owns even if it has not been hydrated into the store yet
		let ids: string[];
		try {
			const all = await DatabaseService.getAllConversations();
			ids = all.map((c) => c.id).filter((id) => !!id);
		} catch (e) {
			console.warn('syncRemoteRunningStreams DB read failed:', e);
			return;
		}
		// only ask about conv ids the user already owns
		if (ids.length === 0) {
			for (const id of Array.from(this.remoteRunningConvs)) {
				this.remoteRunningConvs.delete(id);
			}
			return;
		}
		// rebuild the frozen conv::model identity per conv so a session started with a model still
		// matches. the server response is mapped back to the bare id below for the sidebar set
		const lookupIds = ids.map((id) =>
			ChatService.resumeStreamIdentity(id, ChatService.getStreamState(id), null)
		);
		let sessions: ApiStreamSession[];
		try {
			const resp = await fetch('./v1/streams/lookup', {
				method: 'POST',
				headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
				body: JSON.stringify({ conversation_ids: lookupIds })
			});
			if (!resp.ok) return;
			const body = (await resp.json()) as unknown;
			if (!Array.isArray(body)) return;
			sessions = body as ApiStreamSession[];
		} catch (e) {
			console.warn('syncRemoteRunningStreams fetch failed:', e);
			return;
		}
		const running = new SvelteSet<string>();
		for (const s of sessions) {
			if (s && !s.is_done && typeof s.conversation_id === 'string' && s.conversation_id) {
				// strip the optional ::model suffix, the sidebar set is keyed by the bare conv id
				const sepIdx = s.conversation_id.indexOf('::');
				const bareId = sepIdx === -1 ? s.conversation_id : s.conversation_id.slice(0, sepIdx);
				running.add(bareId);
			}
		}
		for (const id of Array.from(this.remoteRunningConvs)) {
			if (!running.has(id)) {
				this.remoteRunningConvs.delete(id);
			}
		}
		for (const id of running) {
			this.remoteRunningConvs.add(id);
		}
	}

	getChatStreamingPublic(convId: string): { response: string; messageId: string } | undefined {
		return this.getChatStreaming(convId);
	}

	isChatLoadingPublic(convId: string): boolean {
		return this.chatLoadingStates.get(convId) || false;
	}

	isChatReasoningPublic(convId: string): boolean {
		return this.chatReasoningStates.get(convId) || false;
	}

	private isChatLoadingInternal(convId: string): boolean {
		return this.chatLoadingStates.has(convId) || this.chatStreamingStates.has(convId);
	}

	hasPendingMessage(convId: string): boolean {
		return this._pendingMessages.has(convId);
	}

	pendingMessageContent(convId: string): string | null {
		return this._pendingMessages.get(convId)?.content ?? null;
	}

	pendingMessageExtras(convId: string): DatabaseMessageExtra[] | undefined {
		return this._pendingMessages.get(convId)?.extras;
	}

	injectPendingMessage(convId: string, content: string, extras?: DatabaseMessageExtra[]): void {
		this._pendingMessages.set(convId, { content, extras });
	}

	clearPendingMessage(convId: string): void {
		this._pendingMessages.delete(convId);
	}

	consumePendingMessage(
		convId: string
	): { content: string; extras?: DatabaseMessageExtra[] } | null {
		const msg = this._pendingMessages.get(convId);
		if (!msg) return null;
		this._pendingMessages.delete(convId);
		return msg;
	}

	private touchConversationState(convId: string): void {
		this.conversationStateTimestamps.set(convId, { lastAccessed: Date.now() });
	}

	cleanupOldConversationStates(activeConversationIds?: string[]): number {
		const now = Date.now();
		const activeIdsList = activeConversationIds ?? [];
		const preserveIds = this.activeConversationId
			? [...activeIdsList, this.activeConversationId]
			: activeIdsList;
		const allConvIds = [
			...new Set([
				...this.chatLoadingStates.keys(),
				...this.chatStreamingStates.keys(),
				...this.abortControllers.keys(),
				...this.processingStates.keys(),
				...this.conversationStateTimestamps.keys()
			])
		];
		const cleanupCandidates: Array<{ convId: string; lastAccessed: number }> = [];
		for (const convId of allConvIds) {
			if (preserveIds.includes(convId)) continue;
			if (this.chatLoadingStates.get(convId)) continue;
			if (this.chatStreamingStates.has(convId)) continue;
			const ts = this.conversationStateTimestamps.get(convId);
			cleanupCandidates.push({ convId, lastAccessed: ts?.lastAccessed ?? 0 });
		}
		cleanupCandidates.sort((a, b) => a.lastAccessed - b.lastAccessed);
		let cleanedUp = 0;
		for (const { convId, lastAccessed } of cleanupCandidates) {
			if (
				cleanupCandidates.length - cleanedUp > MAX_INACTIVE_CONVERSATION_STATES ||
				now - lastAccessed > INACTIVE_CONVERSATION_STATE_MAX_AGE_MS
			) {
				this.cleanupConversationState(convId);
				cleanedUp++;
			}
		}
		return cleanedUp;
	}
	private cleanupConversationState(convId: string): void {
		const c = this.abortControllers.get(convId);
		if (c && !c.signal.aborted) c.abort();
		this.chatLoadingStates.delete(convId);
		this.chatStreamingStates.delete(convId);
		this.abortControllers.delete(convId);
		this.processingStates.delete(convId);
		this.conversationStateTimestamps.delete(convId);
	}
	getTrackedConversationCount(): number {
		return new Set([
			...this.chatLoadingStates.keys(),
			...this.chatStreamingStates.keys(),
			...this.abortControllers.keys(),
			...this.processingStates.keys()
		]).size;
	}

	private getMessageByIdWithRole(
		messageId: string,
		expectedRole?: MessageRole
	): { message: DatabaseMessage; index: number } | null {
		const index = conversationsStore.findMessageIndex(messageId);
		if (index === -1) return null;
		const message = conversationsStore.activeMessages[index];
		if (expectedRole && message.role !== expectedRole) return null;
		return { message, index };
	}

	async addMessage(
		role: MessageRole,
		content: string,
		type: MessageType = MessageType.TEXT,
		parent: string = '-1',
		extras?: DatabaseMessageExtra[],
		isHidden?: boolean
	): Promise<DatabaseMessage> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv) throw new Error('No active conversation');
		let parentId: string | null = null;
		if (parent === '-1') {
			const am = conversationsStore.activeMessages;
			if (am.length > 0) parentId = am[am.length - 1].id;
			else {
				const all = await conversationsStore.getConversationMessages(activeConv.id);
				const r = all.find((m) => m.parent === null && m.type === 'root');
				parentId = r ? r.id : await DatabaseService.createRootMessage(activeConv.id);
			}
		} else parentId = parent;
		const message = await DatabaseService.createMessageBranch(
			{
				convId: activeConv.id,
				role,
				content,
				type,
				timestamp: Date.now(),
				toolCalls: '',
				children: [],
				extra: extras || [],
				isHidden: isHidden
			},
			parentId
		);
		conversationsStore.addMessageToActive(message);
		await conversationsStore.updateCurrentNode(message.id);
		conversationsStore.updateConversationTimestamp();
		return message;
	}

	async addSystemPrompt(): Promise<void> {
		let activeConv = conversationsStore.activeConversation;
		if (!activeConv) {
			await conversationsStore.createConversation();
			activeConv = conversationsStore.activeConversation;
		}
		if (!activeConv) return;
		try {
			const allMessages = await conversationsStore.getConversationMessages(activeConv.id);
			const rootMessage = allMessages.find((m) => m.type === 'root' && m.parent === null);
			const rootId = rootMessage
				? rootMessage.id
				: await DatabaseService.createRootMessage(activeConv.id);
			const existingSystemMessage = allMessages.find(
				(m) => m.role === MessageRole.SYSTEM && m.parent === rootId
			);
			if (existingSystemMessage) {
				this.pendingEditMessageId = existingSystemMessage.id;
				if (!conversationsStore.activeMessages.some((m) => m.id === existingSystemMessage.id))
					conversationsStore.activeMessages.unshift(existingSystemMessage);
				return;
			}
			const am = conversationsStore.activeMessages;
			const firstActiveMessage = am.find((m) => m.parent === rootId);
			const systemMessage = await DatabaseService.createSystemMessage(
				activeConv.id,
				SYSTEM_MESSAGE_PLACEHOLDER,
				rootId
			);
			if (firstActiveMessage) {
				await DatabaseService.updateMessage(firstActiveMessage.id, {
					parent: systemMessage.id
				});
				await DatabaseService.updateMessage(systemMessage.id, {
					children: [firstActiveMessage.id]
				});
				const updatedRootChildren = rootMessage
					? rootMessage.children.filter((id: string) => id !== firstActiveMessage.id)
					: [];
				await DatabaseService.updateMessage(rootId, {
					children: [
						...updatedRootChildren.filter((id: string) => id !== systemMessage.id),
						systemMessage.id
					]
				});
				const firstMsgIndex = conversationsStore.findMessageIndex(firstActiveMessage.id);
				if (firstMsgIndex !== -1)
					conversationsStore.updateMessageAtIndex(firstMsgIndex, {
						parent: systemMessage.id
					});
			}
			conversationsStore.activeMessages.unshift(systemMessage);
			this.pendingEditMessageId = systemMessage.id;
			conversationsStore.updateConversationTimestamp();
		} catch (error) {
			console.error('Failed to add system prompt:', error);
		}
	}

	async removeSystemPromptPlaceholder(messageId: string): Promise<boolean> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv) return false;
		try {
			const allMessages = await conversationsStore.getConversationMessages(activeConv.id);
			const systemMessage = findMessageById(allMessages, messageId);
			if (!systemMessage || systemMessage.role !== MessageRole.SYSTEM) return false;
			const rootMessage = allMessages.find((m) => m.type === 'root' && m.parent === null);
			if (!rootMessage) return false;
			if (allMessages.length === 2 && systemMessage.children.length === 0) {
				await conversationsStore.deleteConversation(activeConv.id);
				return true;
			}
			for (const childId of systemMessage.children) {
				await DatabaseService.updateMessage(childId, { parent: rootMessage.id });
				const childIndex = conversationsStore.findMessageIndex(childId);
				if (childIndex !== -1)
					conversationsStore.updateMessageAtIndex(childIndex, { parent: rootMessage.id });
			}
			await DatabaseService.updateMessage(rootMessage.id, {
				children: [
					...rootMessage.children.filter((id: string) => id !== messageId),
					...systemMessage.children
				]
			});
			await DatabaseService.deleteMessage(messageId);
			const systemIndex = conversationsStore.findMessageIndex(messageId);
			if (systemIndex !== -1) conversationsStore.activeMessages.splice(systemIndex, 1);
			conversationsStore.updateConversationTimestamp();
			return false;
		} catch (error) {
			console.error('Failed to remove system prompt placeholder:', error);
			return false;
		}
	}

	private async createAssistantMessage(parentId?: string): Promise<DatabaseMessage> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv) throw new Error('No active conversation');
		return await DatabaseService.createMessageBranch(
			{
				convId: activeConv.id,
				type: MessageType.TEXT,
				role: MessageRole.ASSISTANT,
				content: '',
				timestamp: Date.now(),
				toolCalls: '',
				children: [],
				model: null
			},
			parentId || null
		);
	}

	/**
	 * Conversation auto summarization and memory extraction
	 *
	 * @param forceRun - Force run summarization even if unsummarized messages < 30
	 */
	async updateAIHistory(forceRun = false): Promise<void> {
		console.trace('[DEBUG_TRACE] updateAIHistory called. forceRun:', forceRun);
		if (this.isSummarizing) return;
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv) return;

		const currentConfig = config();
		const activeMessages = conversationsStore.activeMessages;
		// root や system を除いた実際のやり取りの件数をカウント
		const displayMessages = activeMessages.filter(
			(m) => m.type !== 'root' && m.role !== 'system' && !m.isHidden
		);

		const lastIdx = activeConv.lastSummarizedIndex ?? 0;
		const totalMsgs = displayMessages.length;
		const unsummarized = totalMsgs - lastIdx;

		// 判定の前にすべての詳細な状態ログをコンソールへ出力
		console.log('[DEBUG_SUMMARIZE_CHECK]', {
			forceRun,
			totalMsgs,
			lastIdx,
			unsummarized,
			autoSummarizeSetting: currentConfig.autoSummarize,
			displayMessagesDetail: displayMessages.map(m => ({
				id: m.id,
				role: m.role,
				type: m.type,
				isHidden: m.isHidden,
				contentExcerpt: m.content ? m.content.slice(0, 20) : ''
			}))
		});

		// 自動要約がオフで、かつ強制実行でもない場合はスキップ
		if (!currentConfig.autoSummarize && !forceRun) {
			console.log('[DEBUG_SUMMARIZE_SKIP] autoSummarize is disabled and not forceRun');
			return;
		}

		// 手動（強制実行）でない場合、未要約メッセージが30件未満ならスキップ
		if (!forceRun && unsummarized < 30) {
			console.log('[DEBUG_SUMMARIZE_SKIP] unsummarized messages < 30:', unsummarized);
			return;
		}

		// 直近10件を残して要約
		const endIdx = forceRun ? totalMsgs : Math.max(0, totalMsgs - 10);
		if (lastIdx >= endIdx) {
			return;
		}

		this.isSummarizing = true;
		this.summarizeStatusText = t('toast.organizingMemory', { seconds: 0 }, 'Organizing memory... 0s');
		const startTime = Date.now();

		const timer = setInterval(() => {
			const elapsed = Math.floor((Date.now() - startTime) / 1000);
			this.summarizeStatusText = t('toast.organizingMemory', { seconds: elapsed }, `Organizing memory... ${elapsed}s`);
		}, 1000);

		const messagesToSummarize = displayMessages.slice(lastIdx, endIdx);

		try {
			const targetMsgId = displayMessages[endIdx - 1]?.id || activeConv.currNode || '';
			let logText = '';
			for (const msg of messagesToSummarize) {
				let content = msg.content;
				// <think>タグを除去
				content = content.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
				if (!content) content = '(空のメッセージ)';

				let imageNote = '';
				if (msg.extra?.attachments && msg.extra.attachments.some(a => a.type === AttachmentType.IMAGE || a.mimeType?.startsWith('image/'))) {
					const imageNames = msg.extra.attachments
						.filter(a => a.type === AttachmentType.IMAGE || a.mimeType?.startsWith('image/'))
						.map(a => a.name || '画像')
						.join(', ');
					imageNote = ` [送信画像: ${imageNames}]`;
				}

				logText += `${msg.role === MessageRole.USER ? 'ユーザー' : 'AI'}: ${content}${imageNote}\n`;
			}

			const aiHistoryTextList = activeConv.aiHistoryTextList ?? [];
			const prevSummary = aiHistoryTextList.length > 0
				? `\n## Previous Context (Summary Background)\n${aiHistoryTextList.map((s, idx) => `[Episode ${idx + 1}]: ${s}`).join('\n\n')}\n`
				: `\nNo previous summary available.`;

			const fullPrompt = `${prevSummary}\n## New Conversation Log\n${logText}\n## Output (Provide only the summary text matching the conversation language):`;
			const factPrompt = `## Conversation Log\n${logText}\n## Output (Extract facts in the conversation language):`;

			const sumCfg = getSummaryConfig();

			const summaryModel = currentConfig.summaryModel?.toString().trim() || 'main';
			const isSummaryModelMain = !summaryModel || summaryModel === 'main';

			// --- 1. あらすじ要約の実行 (Rebuild経路同様の再試行付き: 失敗時は温度0.7、最大2回) ---
			let summaryText = '';
			this.lastSummaryReasoning = '';
			let attempts = 0;
			const maxAttempts = 2;

			while (!summaryText && attempts < maxAttempts) {
				attempts++;
				try {
					if (isSummaryModelMain) {
						const response = await fetch(API_CHAT.COMPLETIONS, {
							method: 'POST',
							headers: {
								'Content-Type': 'application/json',
								...getAuthHeaders()
							},
							body: JSON.stringify({
								model: selectedModelName() || undefined,
								messages: [
									{
										role: 'system',
										content: sumCfg.summaryPrompt
									},
									{
										role: 'user',
										content: fullPrompt
									}
								],
								stream: false,
								temperature: attempts > 1 ? 0.7 : sumCfg.summaryTemp,
								max_tokens: sumCfg.summaryMaxTokens,
								chat_template_kwargs: { enable_thinking: false }
							})
						});

						if (response.ok) {
							const resData = await response.json();
							const rawContent = resData.choices?.[0]?.message?.content || '';
							// 思考プロセスの抽出
							const thinkMatch = rawContent.match(/<think>([\s\S]*?)<\/think>/i);
							if (thinkMatch) {
								this.lastSummaryReasoning = thinkMatch[1].trim();
							}
							summaryText = rawContent.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
						} else {
							console.warn(`[AutoSummarize] Failed to summarize using main model (Attempt ${attempts}):`, response.statusText);
						}
					} else {
						const response = await fetch('http://localhost:11434/api/chat', {
							method: 'POST',
							headers: { 'Content-Type': 'application/json' },
							body: JSON.stringify({
								model: summaryModel,
								messages: [
									{
										role: 'system',
										content: sumCfg.summaryPrompt
									},
									{
										role: 'user',
										content: fullPrompt
									}
								],
								stream: false,
								options: {
									temperature: attempts > 1 ? 0.7 : sumCfg.summaryTemp,
									num_predict: sumCfg.summaryMaxTokens
								}
							})
						});

						if (response.ok) {
							const resData = await response.json();
							const rawContent = resData.message?.content || '';
							// 思考プロセスの抽出
							const thinkMatch = rawContent.match(/<think>([\s\S]*?)<\/think>/i);
							if (thinkMatch) {
								this.lastSummaryReasoning = thinkMatch[1].trim();
							}
							summaryText = rawContent.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
						} else {
							console.warn(`[AutoSummarize] Failed to summarize using Ollama (Attempt ${attempts}):`, response.statusText);
						}
					}
				} catch (sumErr) {
					console.error(`[AutoSummarize] Error during summary generation (Attempt ${attempts}):`, sumErr);
				}
			}

			this.lastSummaryOutput = summaryText;

			if (!summaryText || !summaryText.trim()) {
				const currentFails = (this.summaryFailuresByConv.get(activeConv.id) || 0) + 1;
				this.summaryFailuresByConv.set(activeConv.id, currentFails);
				const unsummarizedCount = displayMessages.length - (activeConv.lastSummarizedIndex ?? 0);

				if (currentFails >= 3 || unsummarizedCount >= 38) {
					console.warn(`[AutoSummarize] 連続${currentFails}回失敗、または未要約件数(${unsummarizedCount}件)上限に達したため、フォールバックブロックを作成してインデックスを進めます。`);
					const firstMsgContent = messagesToSummarize[0]?.content?.replace(/<think>[\s\S]*?<\/think>/gi, '').trim().slice(0, 40) || '';
					const lastMsgContent = messagesToSummarize[messagesToSummarize.length - 1]?.content?.replace(/<think>[\s\S]*?<\/think>/gi, '').trim().slice(0, 40) || '';
					summaryText = `【エピソード整理】ユーザーとAIの会話展開（"${firstMsgContent}..." 〜 "${lastMsgContent}..." に関するやり取り）。`;
					this.summaryFailuresByConv.set(activeConv.id, 0);
				} else {
					console.warn(`[AutoSummarize] 要約文の生成に失敗 (${currentFails}/3回目, 未要約${unsummarizedCount}件)。生ログを保持し、インデックスは進めずに次回ターンで再試行します。`);
					return;
				}
			} else {
				this.summaryFailuresByConv.set(activeConv.id, 0);
			}

			let indexUpdated = false;
			try {
				// 要約生成に成功した場合のみ、要約ブロックを追加して lastSummarizedIndex を進める（メッセージ消失防止）
				const dbUpdates: Record<string, any> = {
					lastSummarizedIndex: endIdx,
					aiHistoryText: summaryText
				};
				let currentList = [...(activeConv.aiHistoryTextList ?? [])];
				let isSlidingActive = activeConv.isSlidingActive ?? false;

				currentList.push(summaryText);

					// リストサイズが最大値（MAX_SUMMARY_BLOCKS）に達したか検知
					if (currentList.length >= MAX_SUMMARY_BLOCKS) {
						const forgottenBlocks: string[] = [];
						while (currentList.length > (MAX_SUMMARY_BLOCKS - FORGET_BLOCKS_COUNT)) {
							const forgotten = currentList.shift();
							if (forgotten) {
								forgottenBlocks.push(forgotten);
							}
						}
						isSlidingActive = true;

						// 押し出された要約ブロックを親子リトリーバル用エピソード記憶としてアーカイブ（非同期）
						if (forgottenBlocks.length > 0) {
							for (const blockText of forgottenBlocks) {
								this.archiveForgottenBlockToRag(blockText, activeConv.id, targetMsgId);
							}
						}
					}

				dbUpdates.aiHistoryTextList = currentList;
				dbUpdates.isSlidingActive = isSlidingActive;

				await DatabaseService.updateConversation(activeConv.id, dbUpdates);
				conversationsStore.activeConversation = {
					...activeConv,
					...dbUpdates
				};

				// 要約生成中に増えた新しいメッセージ（要約対象以降）も含め、
				// アクティブなパス上のすべての末尾メッセージのスナップショットを最新に更新する
				const currentActiveMessages = (conversationsStore.activeConversation && conversationsStore.activeMessages.length > 0)
					? conversationsStore.activeMessages
					: displayMessages;
				const currentDisplayMsgs = currentActiveMessages.filter(
					(m) => m.role !== MessageRole.SYSTEM && !(m.role === MessageRole.USER && m.type === MessageType.SYSTEM)
				);

				for (let i = endIdx - 1; i < currentDisplayMsgs.length; i++) {
					const msg = currentDisplayMsgs[i];
					if (msg) {
						const msgUpdates: Record<string, any> = {
							lastSummarizedIndexSnapshot: endIdx,
							aiHistoryTextListSnapshot: currentList,
							isSlidingActiveSnapshot: isSlidingActive
						};
						if (summaryText) {
							msgUpdates.aiHistoryTextSnapshot = summaryText;
						}
						Object.assign(msg, msgUpdates);
						await DatabaseService.updateMessage(msg.id, msgUpdates);
					}
				}
				indexUpdated = true;
			} catch (dbErr) {
				console.error('要約結果のDBインデックス更新中にエラー:', dbErr);
			}

			// --- 2. 事実抽出 (RAG) の実行 ---
			let factText = '';
			this.lastFactReasoning = '';
			console.log('🔍 [FACT_EXTRACT_START] Requesting LLM for fact extraction...', {
				summaryModel,
				isSummaryModelMain,
				session_id: activeConv.id
			});

			if (isSummaryModelMain) {
				const response = await fetch(API_CHAT.COMPLETIONS, {
					method: 'POST',
					headers: {
						'Content-Type': 'application/json',
						...getAuthHeaders()
					},
					body: JSON.stringify({
						model: selectedModelName() || undefined,
						messages: [
							{
								role: 'system',
								content: sumCfg.factPrompt
							},
							{
								role: 'user',
								content: factPrompt
							}
						],
						stream: false,
						temperature: sumCfg.factTemp,
						max_tokens: sumCfg.factMaxTokens,
						chat_template_kwargs: { enable_thinking: false }
					})
				});

				if (response.ok) {
					const resData = await response.json();
					const msgObj = resData.choices?.[0]?.message;
					const rawContent = msgObj?.content || '';
					console.log('📝 [FACT_EXTRACT_RAW_RESPONSE]', rawContent);
					// 思考プロセスの抽出
					const thinkMatch = rawContent.match(/<think>([\s\S]*?)<\/think>/i);
					if (thinkMatch) {
						this.lastFactReasoning = thinkMatch[1].trim();
						console.log('🧠 [FACT_EXTRACT_THINKING]', this.lastFactReasoning);
					}
					factText = rawContent.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
				} else {
					console.error('❌ [FACT_EXTRACT_ERROR] LLM API response not ok:', response.status, response.statusText);
				}
			} else {
				const response = await fetch('http://localhost:11434/api/chat', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({
						model: summaryModel,
						messages: [
							{
								role: 'system',
								content: sumCfg.factPrompt
							},
							{
								role: 'user',
								content: factPrompt
							}
						],
						stream: false,
						options: {
							temperature: sumCfg.factTemp,
							num_predict: sumCfg.factMaxTokens
						}
					})
				});

				if (response.ok) {
					const resData = await response.json();
					const rawContent = resData.message?.content || '';
					// 思考プロセスの抽出
					const thinkMatch = rawContent.match(/<think>([\s\S]*?)<\/think>/i);
					if (thinkMatch) {
						this.lastFactReasoning = thinkMatch[1].trim();
					}
					factText = rawContent.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
				}
			}

			this.lastFactOutput = factText;

			if (factText) {
				const facts = factText.split('\n')
					.map(line => line.trim())
					// 1. 箇条書き記号（-, *, ・）で始まる行のみを対象とする
					.filter(line => /^[\-\*・]\s*/.test(line))
					// 2. 箇条書き記号をトリム
					.map(line => line.replace(/^[\-\*・]\s*/, '').trim())
					// 3. 空行および「なし」を除外
					.filter(line => {
						if (line.length === 0) return false;
						const normalized = line.replace(/[\.。、，\,]/g, '').trim();
						const noSpace = normalized.replace(/\s+/g, '');
						return !(
							noSpace === 'なし' ||
							noSpace === '無し' ||
							noSpace === '特になし' ||
							noSpace === '特に無し' ||
							noSpace === '特にありません' ||
							noSpace === 'ありません' ||
							noSpace === '該当なし' ||
							noSpace === '該当無し' ||
							noSpace === '新しい事実はありません' ||
							noSpace === '抽出可能な事実はありません'
						);
					});

				if (facts.length > 0) {
					const backendHost = (typeof window !== 'undefined' && window.location.hostname) ? window.location.hostname : '127.0.0.1';
					const memoryApiUrl = `http://${backendHost}:5000/memory/add?session_id=${activeConv.id}${targetMsgId ? `&message_id=${targetMsgId}` : ''}`;
					console.log(`🧠 [RAG Fact Add] Sending ${facts.length} facts to ChromaDB -> ${memoryApiUrl} (msgId: ${targetMsgId})`, facts);
					try {
						const memoryRes = await fetch(memoryApiUrl, {
							method: 'POST',
							headers: { 'Content-Type': 'application/json' },
							body: JSON.stringify({
								texts: facts,
								message_id: targetMsgId,
								active_message_ids: displayMessages.map(m => m.id)
							})
						});
						if (memoryRes.ok) {
							const memData = await memoryRes.json();
							console.log('✅ [RAG Fact Add] Successfully added facts to ChromaDB:', memData);
							if (typeof window !== 'undefined') {
								window.dispatchEvent(new CustomEvent('memoryUpdated'));
							}
						} else {
							console.error('❌ [RAG Fact Add] Failed to add memory:', memoryRes.statusText);
						}
					} catch (memErr) {
						console.error('❌ [RAG Fact Add] Network error adding memory to ChromaDB:', memErr);
					}
				} else {
					console.log('ℹ️ [RAG Fact Add] No valid new facts extracted from summary log.');
				}
			}
		} catch (error) {
			console.error('要約・事実抽出処理エラー:', error);
			if (!indexUpdated) {
				const currentFails = (this.summaryFailuresByConv.get(activeConv.id) || 0) + 1;
				this.summaryFailuresByConv.set(activeConv.id, currentFails);
				const unsummarizedCount = displayMessages.length - (activeConv.lastSummarizedIndex ?? 0);
				if (currentFails >= 3 || unsummarizedCount >= 38) {
					console.warn(`[AutoSummarize Catch] 連続${currentFails}回失敗または未要約上限(${unsummarizedCount}件)到達。フォールバック処理を実行します。`);
					try {
						const firstMsgContent = messagesToSummarize[0]?.content?.replace(/<think>[\s\S]*?<\/think>/gi, '').trim().slice(0, 40) || '';
						const lastMsgContent = messagesToSummarize[messagesToSummarize.length - 1]?.content?.replace(/<think>[\s\S]*?<\/think>/gi, '').trim().slice(0, 40) || '';
						const fallbackSummary = `【エピソード整理】ユーザーとAIの会話展開（"${firstMsgContent}..." 〜 "${lastMsgContent}..." に関するやり取り）。`;
						const currentList = [...(activeConv.aiHistoryTextList ?? []), fallbackSummary];
						const dbUpdates: Record<string, any> = {
							lastSummarizedIndex: endIdx,
							aiHistoryText: fallbackSummary,
							aiHistoryTextList: currentList,
							isSlidingActive: activeConv.isSlidingActive ?? false
						};
						await DatabaseService.updateConversation(activeConv.id, dbUpdates);
						conversationsStore.activeConversation = {
							...activeConv,
							...dbUpdates
						};
						const currentActiveMessages = conversationsStore.activeConversation
							? this.getActiveMessages(conversationsStore.activeConversation)
							: displayMessages;
						const currentDisplayMsgs = currentActiveMessages.filter(
							(m) => m.role !== MessageRole.SYSTEM && !(m.role === MessageRole.USER && m.type === MessageType.SYSTEM)
						);

						for (let i = endIdx - 1; i < currentDisplayMsgs.length; i++) {
							const msg = currentDisplayMsgs[i];
							if (msg) {
								await DatabaseService.updateMessage(msg.id, {
									lastSummarizedIndexSnapshot: endIdx,
									aiHistoryTextListSnapshot: currentList,
									isSlidingActiveSnapshot: activeConv.isSlidingActive ?? false
								});
							}
						}
						this.summaryFailuresByConv.set(activeConv.id, 0);
					} catch (dbErr) {
						console.error('[AutoSummarize Catch] フォールバック更新失敗:', dbErr);
					}
				} else {
					console.warn(`[AutoSummarize Catch] 要約エラー発生 (${currentFails}/3回目, 未要約${unsummarizedCount}件)。生ログを保持し、インデックスは進めません。`);
				}
			}
		} finally {
			clearInterval(timer);
			this.isSummarizing = false;
			this.summarizeStatusText = '';
		}
	}

	/**
	 * 押し出された要約ブロックを親子リトリーバル用エピソード記憶としてChromaDBにアーカイブ
	 */
	async archiveForgottenBlockToRag(parentText: string, convId: string, messageId: string) {
		if (!parentText || !convId) return;

		const archiveId = `arch_${convId}_${messageId}_${Date.now()}`;
		const entry: ArchivedSummaryEntry = {
			id: archiveId,
			messageId: messageId,
			parentText: parentText,
			timestamp: Date.now(),
			status: 'pending'
		};

		try {
			// 1. IndexedDB に status: 'pending' として実体先行永続化（Self-Healingの拠り所）
			const conv = await DatabaseService.getConversation(convId);
			if (conv) {
				const existingArchives = conv.archivedSummaries ?? [];
				if (!existingArchives.some((a) => a.parentText === parentText && a.messageId === messageId)) {
					const updatedArchives = [...existingArchives, entry];
					await DatabaseService.updateConversation(convId, { archivedSummaries: updatedArchives });
					if (conversationsStore.activeConversation?.id === convId) {
						conversationsStore.activeConversation = {
							...conversationsStore.activeConversation,
							archivedSummaries: updatedArchives
						};
					}
				}
			}

			// 2. 全角・半角のカギ括弧や引用符でくくられたキーワードを正規表現で即座に抽出（追加LLM通信ゼロ・オーバーヘッド0秒）
			const cleanBlock = parentText.replace(/^\[エピソード\s*\d+\]:\s*/, '').trim();
			const bracketRegex = /(?:[「『｢【［\[“"‘'])(.*?)(?:[」』｣】］\]”"’'])/g;
			const bracketMatches = [...cleanBlock.matchAll(bracketRegex)]
				.map((m) => m[1].trim())
				.filter((t) => t.length > 0 && t.length < 50 && !t.includes('\n'));

			// 重複排除して最大 2〜3 個に厳選
			const uniqueKeys = Array.from(new Set(bracketMatches));
			let childTexts: string[] = uniqueKeys.slice(0, 3);

			// 3. ルールベース・フォールバック（要約に「」がなかった場合のバックアップ）
			if (childTexts.length === 0) {
				const sentences = cleanBlock.split(/[。\n]+/).map((s) => s.trim()).filter((s) => s.length > 8);
				childTexts = sentences.slice(0, 2);
				if (childTexts.length === 0 && cleanBlock.length > 0) {
					childTexts = [cleanBlock.slice(0, 80)];
				}
				console.log('ℹ️ [ParentChild Fallback Keys (No brackets found)]', childTexts);
			} else {
				console.log('✨ [ParentChild Extracted Keys from 「」]', childTexts);
			}

			// 4. ChromaDB API /memory/add_parent_child に直接送信
			const backendHost = (typeof window !== 'undefined' && window.location.hostname) ? window.location.hostname : '127.0.0.1';
			const addRes = await fetch(`http://${backendHost}:5000/memory/add_parent_child?session_id=${convId}`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					session_id: convId,
					message_id: messageId,
					parent_text: parentText,
					child_texts: childTexts,
					channel_id: 'web_ui'
				})
			});

			if (addRes.ok) {
				console.log(`🧠 [Parent-Child Archived] Successfully saved episode for msg: ${messageId}`, { childTexts });
				// 5. IndexedDB の status を 'completed' に更新
				const freshConv = await DatabaseService.getConversation(convId);
				if (freshConv && freshConv.archivedSummaries) {
					const updated = freshConv.archivedSummaries.map((a) =>
						(a.id === archiveId || (a.parentText === parentText && a.messageId === messageId))
							? { ...a, status: 'completed' as const }
							: a
					);
					await DatabaseService.updateConversation(convId, { archivedSummaries: updated });
					if (conversationsStore.activeConversation?.id === convId) {
						conversationsStore.activeConversation = {
							...conversationsStore.activeConversation,
							archivedSummaries: updated
						};
					}
				}

				// 6. RAGパネル再描画イベント発火
				if (typeof window !== 'undefined') {
					window.dispatchEvent(new CustomEvent('memoryUpdated'));
				}
			} else {
				console.error('❌ [Parent-Child Archive Failed]:', addRes.statusText);
			}
		} catch (e) {
			console.error('Failed to archive forgotten block to RAG:', e);
		}
	}

	async sendMessage(
		content: string,
		extras?: DatabaseMessageExtra[],
		isHidden?: boolean
	): Promise<void> {
		if (this.isSummarizing) {
			console.warn('Cannot send message during summarization / memory extraction.');
			return;
		}
		if (!content.trim() && (!extras || extras.length === 0)) return;
		const activeConv = conversationsStore.activeConversation;

		// 隠しメッセージ（アバターからの自動送信）の場合：
		// 既にストリーミング中・処理中の場合は送信しない（キューにも入れない）
		if (isHidden) {
			if (activeConv) {
				if (this.isChatLoadingInternal(activeConv.id)) return;
				if (agenticStore.isRunning(activeConv.id)) return;
			}
		}

		// If agentic loop is running, inject as a steering message instead of starting a new flow
		if (activeConv && agenticStore.isRunning(activeConv.id)) {
			agenticStore.injectSteeringMessage(activeConv.id, content, extras);
			return;
		}

		// If non-agentic streaming is active, queue as a pending message to send after completion
		if (activeConv && this.isChatLoadingInternal(activeConv.id)) {
			this.injectPendingMessage(activeConv.id, content, extras);
			return;
		}

		// Cancel any in-flight pre-encode request
		this.cancelPreEncode();

		// Consume MCP resource attachments - converts them to extras and clears the live store
		// (隠しメッセージの場合はMCPリソースを消費しない)
		const resourceExtras = isHidden ? [] : mcpStore.consumeResourceAttachmentsAsExtras();
		const allExtras = resourceExtras.length > 0 ? [...(extras || []), ...resourceExtras] : extras;

		let isNewConversation = false;
		if (!activeConv) {
			await conversationsStore.createConversation();
			isNewConversation = true;
		}
		const currentConv = conversationsStore.activeConversation;
		if (!currentConv) return;
		this.showErrorDialog(null);
		this.setChatLoading(currentConv.id, true);
		this.clearChatStreaming(currentConv.id);
		try {
			let parentIdForUserMessage: string | undefined;
			if (isNewConversation) {
				const rootId = await DatabaseService.createRootMessage(currentConv.id);
				const currentConfig = config();
				let systemPrompt = currentConfig.systemMessage?.toString().trim() || '';
				const userPersona = currentConfig.userPersona?.toString().trim();

				if (userPersona) {
					systemPrompt = systemPrompt
						? `${systemPrompt}\n\n[User Persona / Information]\n${userPersona}`
						: `[User Persona / Information]\n${userPersona}`;
				}

				if (systemPrompt) {
					const systemMessage = await DatabaseService.createSystemMessage(
						currentConv.id,
						systemPrompt,
						rootId
					);
					conversationsStore.addMessageToActive(systemMessage);
					parentIdForUserMessage = systemMessage.id;
				} else parentIdForUserMessage = rootId;
			}
			const userMessage = await this.addMessage(
				MessageRole.USER,
				content,
				MessageType.TEXT,
				parentIdForUserMessage ?? '-1',
				allExtras,
				isHidden
			);
			if (isNewConversation && content)
				await conversationsStore.updateConversationName(
					currentConv.id,
					generateConversationTitle(content, Boolean(config().titleGenerationUseFirstLine))
				);
			const assistantMessage = await this.createAssistantMessage(userMessage.id);
			conversationsStore.addMessageToActive(assistantMessage);
			await conversationsStore.updateCurrentNode(assistantMessage.id);
			const messagesToSend = conversationsStore.activeMessages.slice(0, -1);

			await this.streamChatCompletion(
				messagesToSend,
				assistantMessage,
				undefined,
				undefined,
				undefined,
				config().titleGenerationUseLLM && isNewConversation ? content : undefined
			);
		} catch (error) {
			if (isAbortError(error)) {
				this.setChatLoading(currentConv.id, false);
				return;
			}
			console.error('Failed to send message:', error);
			this.setChatLoading(currentConv.id, false);
			const dialogType =
				error instanceof Error && error.name === 'TimeoutError'
					? ErrorDialogType.TIMEOUT
					: ErrorDialogType.SERVER;
			const contextInfo = (
				error as Error & { contextInfo?: { n_prompt_tokens: number; n_ctx: number } }
			).contextInfo;
			this.showErrorDialog({
				type: dialogType,
				message: error instanceof Error ? error.message : 'Unknown error',
				contextInfo
			});
		}
	}

	private async streamChatCompletion(
		allMessages: DatabaseMessage[],
		assistantMessage: DatabaseMessage,
		onComplete?: (content: string) => Promise<void>,
		onError?: (error: Error) => void,
		modelOverride?: string | null,
		firstUserMessageContent?: string
	): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		let messagesToSend = [...allMessages];

		// 1. スライディング/要約処理を適用（要約が存在する場合、要約済みの生ログを削ぎ落として二重送信を防ぐ）
		if (activeConv) {
			const lastIdx = activeConv.lastSummarizedIndex ?? 0;
			const hasSummaries = (activeConv.aiHistoryTextList ?? []).length > 0;

			if (hasSummaries && lastIdx > 0) {
				// 要約が存在する場合、lastSummarizedIndexより前の生メッセージを除外（スライス）する
				const systemMsgs = messagesToSend.filter((m) => m.role === MessageRole.SYSTEM);
				const dialogueMsgs = messagesToSend.filter(
					(m) => m.type !== 'root' && m.role !== MessageRole.SYSTEM
				);
				// lastIdx がメッセージ配列長以上の場合、ユーザーの最新発言が消失する致命バグを防止
				// 最低でも直近1件（ユーザーの最新入力）は常にLLMに送信する
				const safeSliceIdx = Math.min(lastIdx, Math.max(0, dialogueMsgs.length - 1));
				const shortTermMsgs = dialogueMsgs.slice(safeSliceIdx);
				messagesToSend = [...systemMsgs, ...shortTermMsgs];
				console.log(`[Consolidation] Summary active. lastIdx=${lastIdx}, dialogueCount=${dialogueMsgs.length}, safeSliceIdx=${safeSliceIdx}, shortTermRemaining=${shortTermMsgs.length}`);
			} else {
				// 要約未構築の巨大会話でコンテキストオーバーフロー（16384超え）を防ぐ安全ガード
				const systemMsgs = messagesToSend.filter((m) => m.role === MessageRole.SYSTEM);
				const dialogueMsgs = messagesToSend.filter(
					(m) => m.type !== 'root' && m.role !== MessageRole.SYSTEM
				);
				if (dialogueMsgs.length > 40) {
					const shortTermMsgs = dialogueMsgs.slice(-40);
					messagesToSend = [...systemMsgs, ...shortTermMsgs];
				}
			}
		}

		// 1.5 要約済みの過去ターンから画像アタッチメントデータをLLM送信ペイロード限定で除外する
		// （※フロントエンド表示およびDB内のメッセージデータからは画像を削除せずそのまま維持）
		if (activeConv) {
			const lastIdx = activeConv.lastSummarizedIndex ?? 0;
			messagesToSend = messagesToSend.map((msg, index) => {
				// lastIdx より前の要約済みメッセージで画像添付が存在する場合、送信用コピーから画像のみ除外
				if (lastIdx > 0 && index < lastIdx && msg.extra?.attachments && msg.extra.attachments.length > 0) {
					const cloned = JSON.parse(JSON.stringify(msg)) as DatabaseMessage;
					if (cloned.extra && cloned.extra.attachments) {
						cloned.extra.attachments = cloned.extra.attachments.filter(
							(att) => att.type !== AttachmentType.IMAGE && !att.mimeType?.startsWith('image/')
						);
					}
					return cloned;
				}
				return msg;
			});
		}

		// 2. 泥濘要約リストと現在時刻をシステムメッセージへ動的注入する前に、
		// Proxyやゲッターなどの副作用を排除したクローンを生成
		let processedMessages = messagesToSend.map((m) => {
			return JSON.parse(JSON.stringify(m)) as DatabaseMessage;
		});
		const systemIdx = processedMessages.findIndex((m) => m.role === MessageRole.SYSTEM);
		let formattedHistory = '';
		if (activeConv) {
			const aiHistoryTextList = activeConv.aiHistoryTextList ?? [];
			if (aiHistoryTextList.length > 0) {
				formattedHistory = "\n\n【これまでのタイムライン（古い記憶は省略されています）】\n"
					+ aiHistoryTextList.map((s, idx) => `[エピソード ${idx + 1}]: ${s}`).join('\n\n');
			}
		}

		// KVキャッシュ(Prefix Caching)を保護するため、System Promptには固定の記憶タイムラインのみ注入する
		if (systemIdx !== -1) {
			const originalSys = processedMessages[systemIdx];
			processedMessages[systemIdx] = {
				...originalSys,
				content: originalSys.content + formattedHistory
			};
		} else if (formattedHistory) {
			// 会話内にシステムメッセージが存在しない場合、勝手にデフォルトプロンプトを動的注入せず、
			// 要約タイムラインが存在する場合のみタイムラインをシステムメッセージとして注入する
			processedMessages.unshift({
				id: 'system-injected-timeline',
				convId: activeConv?.id || 'none',
				role: MessageRole.SYSTEM,
				content: formattedHistory.trim(),
				timestamp: 0,
				type: MessageType.TEXT,
				children: []
			} as DatabaseMessage);
		}
		// KVキャッシュ(Prefix Caching)を100%保護するため、
		// 過去および最新のユーザーメッセージの content は一切変更・加工せずそのままLLMに送信する。

		// 🔍 デバッグ: LLMに送信されるメッセージペイロードの最終状態を出力
		console.log('[DEBUG_PAYLOAD] Final messages to LLM:', {
			totalCount: processedMessages.length,
			roles: processedMessages.map(m => m.role),
			lastThree: processedMessages.slice(-3).map(m => ({
				role: m.role,
				contentPreview: typeof m.content === 'string' ? m.content.slice(0, 80) : '(non-string)',
				id: m.id
			})),
			systemPromptPreview: processedMessages.find(m => m.role === 'system')?.content?.slice(0, 200) || '(NO SYSTEM PROMPT!)',
			lastSummarizedIndex: activeConv?.lastSummarizedIndex,
			aiHistoryTextListLength: (activeConv?.aiHistoryTextList ?? []).length
		});
		// the ::model suffix in the stream identity is only for router mode, where it routes to the
		// owning child. in single-model mode the identity stays the bare conv id so that attach, stop
		// and reattach all agree, regardless of fresh send vs regenerate passing a resolved model
		let effectiveModel: string | null | undefined = undefined;

		if (isRouterMode()) {
			const conversationModel = this.getConversationModel(processedMessages);
			effectiveModel = modelOverride || selectedModelName() || conversationModel;
		}

		if (isRouterMode() && effectiveModel) {
			if (!modelsStore.getModelProps(effectiveModel))
				await modelsStore.fetchModelProps(effectiveModel);
		}

		// Mutable state for the current message being streamed
		let currentMessageId = assistantMessage.id;
		let streamedContent = '';
		let streamedReasoningContent = '';
		let recalledMemoriesData: { facts?: string[]; episodes?: string[]; web?: string[] } | undefined;
		let resolvedModel: string | null = null;
		let modelPersisted = false;
		const convId = assistantMessage.convId;
		// Tracks the last message created in this flow. Used as the parent for the next
		// turn's assistant message so createAssistantMessage does not have to read
		// conversationsStore.activeMessages, which may belong to a different conversation
		// after the user navigates while the loop is still running.
		let lastCreatedInFlow = currentMessageId;
		// freeze the POST identity from t0 so a stop cancels with the exact session key,
		// never a stale or empty model resolved later
		this.setChatStreaming(convId, streamedContent, currentMessageId, effectiveModel);

		const recordModel = (modelName: string | null | undefined, persistImmediately = true): void => {
			if (!modelName) return;
			const n = normalizeModelName(modelName);
			if (!n || n === resolvedModel) return;
			resolvedModel = n;
			const idx = conversationsStore.findMessageIndex(currentMessageId);
			conversationsStore.updateMessageAtIndex(idx, { model: n });
			if (persistImmediately && !modelPersisted) {
				modelPersisted = true;
				DatabaseService.updateMessage(currentMessageId, { model: n }).catch(() => {
					modelPersisted = false;
					resolvedModel = null;
				});
			}
		};

		let completionIdRecorded = false;
		const recordCompletionId = (id: string): void => {
			if (!id || completionIdRecorded) return;
			completionIdRecorded = true;
			const idx = conversationsStore.findMessageIndex(currentMessageId);
			conversationsStore.updateMessageAtIndex(idx, { completionId: id });
			DatabaseService.updateMessage(currentMessageId, { completionId: id }).catch(() => {
				completionIdRecorded = false;
			});
		};

		const updateStreamingUI = () => {
			this.setChatStreaming(convId, streamedContent, currentMessageId, effectiveModel);
			const idx = conversationsStore.findMessageIndex(currentMessageId);
			conversationsStore.updateMessageAtIndex(idx, { content: streamedContent });
		};

		const cleanupStreamingState = () => {
			this.setStreamingActive(false);
			this.setChatLoading(convId, false);
			this.clearChatStreaming(convId, currentMessageId);
			this.setProcessingState(convId, null);
		};

		this.setStreamingActive(true);
		this.setActiveProcessingConversation(convId);
		const abortController = this.getOrCreateAbortController(convId);

		const streamCallbacks: ChatStreamCallbacks = {
			onChunk: (chunk: string) => {
				streamedContent += chunk;
				updateStreamingUI();
				this.setChatReasoning(convId, false);
			},
			onReasoningChunk: (chunk: string) => {
				streamedReasoningContent += chunk;
				// mark streaming state so a stop mid-thinking can persist the partial reasoning
				this.setChatStreaming(convId, streamedContent, currentMessageId, effectiveModel);
				const idx = conversationsStore.findMessageIndex(currentMessageId);
				conversationsStore.updateMessageAtIndex(idx, {
					reasoningContent: streamedReasoningContent
				});
				this.setChatReasoning(convId, true);
			},
			onToolCallsStreaming: (toolCalls) => {
				const idx = conversationsStore.findMessageIndex(currentMessageId);
				conversationsStore.updateMessageAtIndex(idx, {
					toolCalls: JSON.stringify(toolCalls)
				});
			},
			onAttachments: (messageId: string, extras: DatabaseMessageExtra[]) => {
				if (!extras.length) return;
				const idx = conversationsStore.findMessageIndex(messageId);
				if (idx === -1) return;
				const msg = conversationsStore.activeMessages[idx];
				const updatedExtras = [...(msg.extra || []), ...extras];
				conversationsStore.updateMessageAtIndex(idx, { extra: updatedExtras });
				DatabaseService.updateMessage(messageId, { extra: updatedExtras }).catch(console.error);
			},
			onModel: (modelName: string) => recordModel(modelName),
			onCompletionId: (id: string) => recordCompletionId(id),
			onTurnComplete: (intermediateTimings: ChatMessageTimings) => {
				// Update the first assistant message with cumulative agentic timings
				const idx = conversationsStore.findMessageIndex(assistantMessage.id);
				conversationsStore.updateMessageAtIndex(idx, { timings: intermediateTimings });
			},
			onTimings: (timings?: ChatMessageTimings, promptProgress?: ChatMessagePromptProgress) => {
				const tokensPerSecond =
					timings?.predicted_ms && timings?.predicted_n
						? (timings.predicted_n / timings.predicted_ms) * 1000
						: 0;
				this.updateProcessingStateFromTimings(
					{
						prompt_n: timings?.prompt_n || 0,
						prompt_ms: timings?.prompt_ms,
						predicted_n: timings?.predicted_n || 0,
						predicted_per_second: tokensPerSecond,
						cache_n: timings?.cache_n || 0,
						prompt_progress: promptProgress
					},
					convId
				);
			},
			onAssistantTurnComplete: async (
				content: string,
				reasoningContent: string | undefined,
				timings: ChatMessageTimings | undefined,
				toolCalls: import('$lib/types/api').ApiChatCompletionToolCall[] | undefined
			) => {


				const updateData: Record<string, unknown> = {
					content,
					reasoningContent: reasoningContent || undefined,
					toolCalls: toolCalls ? JSON.stringify(toolCalls) : '',
					timings,
					recalledMemories: recalledMemoriesData
				};
				if (resolvedModel && !modelPersisted) updateData.model = resolvedModel;
				await DatabaseService.updateMessage(currentMessageId, updateData);
				const idx = conversationsStore.findMessageIndex(currentMessageId);
				const uiUpdate: Partial<DatabaseMessage> = {
					content,
					reasoningContent: reasoningContent || undefined,
					toolCalls: toolCalls ? JSON.stringify(toolCalls) : '',
					recalledMemories: recalledMemoriesData
				};
				if (timings) uiUpdate.timings = timings;
				if (resolvedModel) uiUpdate.model = resolvedModel;
				// touch the active ui array and node pointer only when this conversation
				// is displayed; otherwise persist the node move straight to the db so a
				// foreign conv's currNode stays untouched
				if (conversationsStore.activeConversation?.id === convId) {
					conversationsStore.updateMessageAtIndex(idx, uiUpdate);
					await conversationsStore.updateCurrentNode(currentMessageId);
				} else {
					await DatabaseService.updateCurrentNode(convId, currentMessageId);
				}
			},
			createToolResultMessage: async (
				toolCallId: string,
				content: string,
				extras?: DatabaseMessageExtra[]
			) => {
				const msg = await DatabaseService.createMessageBranch(
					{
						convId,
						type: MessageType.TEXT,
						role: MessageRole.TOOL,
						content,
						toolCallId,
						timestamp: Date.now(),
						toolCalls: '',
						children: [],
						extra: extras
					},
					currentMessageId
				);
				// mirror into the active store and move the node pointer only when this
				// conversation is displayed; otherwise persist the node move straight to
				// the db for the owning conv so a foreign conv's currNode stays untouched
				if (conversationsStore.activeConversation?.id === convId) {
					conversationsStore.addMessageToActive(msg);
					await conversationsStore.updateCurrentNode(msg.id);
				} else {
					await DatabaseService.updateCurrentNode(convId, msg.id);
				}
				lastCreatedInFlow = msg.id;
				return msg;
			},
			createAssistantMessage: async () => {
				// Reset streaming state for new message
				streamedContent = '';
				streamedReasoningContent = '';

				const msg = await DatabaseService.createMessageBranch(
					{
						convId,
						type: MessageType.TEXT,
						role: MessageRole.ASSISTANT,
						content: '',
						timestamp: Date.now(),
						toolCalls: '',
						children: [],
						model: resolvedModel
					},
					lastCreatedInFlow
				);
				if (conversationsStore.activeConversation?.id === convId) {
					conversationsStore.addMessageToActive(msg);
				}
				currentMessageId = msg.id;
				lastCreatedInFlow = msg.id;
				return msg;
			},
			onFlowComplete: (finalTimings?: ChatMessageTimings) => {
				if (finalTimings) {
					const idx = conversationsStore.findMessageIndex(assistantMessage.id);

					conversationsStore.updateMessageAtIndex(idx, { timings: finalTimings });
					DatabaseService.updateMessage(assistantMessage.id, {
						timings: finalTimings
					}).catch(console.error);
				}

				cleanupStreamingState();

				this.updateAIHistory().catch(console.error);

				if (onComplete) onComplete(streamedContent);
				if (isRouterMode()) modelsStore.fetchRouterModels().catch(console.error);
				// Pre-encode conversation in KV cache for faster next turn
				if (config().preEncodeConversation) {
					this.triggerPreEncode(
						processedMessages,
						assistantMessage,
						streamedContent,
						effectiveModel,
						true
					);
				}
			},
			onError: async (error: Error) => {
				this.setStreamingActive(false);
				if (isAbortError(error)) {
					cleanupStreamingState();
					// If aborted with a pending message (e.g. "Send immediately"), re-send it
					const pending = this.consumePendingMessage(convId);
					if (pending) {
						this.sendMessage(pending.content, pending.extras);
					}
					return;
				}
				console.error('Streaming error:', error);
				// keep whatever was streamed so far, the message stays in memory and in DB
				await this.savePartialResponseIfNeeded(convId);
				cleanupStreamingState();
				this.clearPendingMessage(convId);

				const contextInfo = (
					error as Error & { contextInfo?: { n_prompt_tokens: number; n_ctx: number } }
				).contextInfo;
				this.showErrorDialog({
					type: error.name === 'TimeoutError' ? ErrorDialogType.TIMEOUT : ErrorDialogType.SERVER,
					message: error.message,
					contextInfo
				});
				if (onError) onError(error);
			}
		};

		const perChatOverrides = conversationsStore.activeConversation?.mcpServerOverrides;

		{
			const agenticResult = await agenticStore.runAgenticFlow({
				conversationId: convId,
				messages: processedMessages,
				options: {
					...this.getApiOptions(),
					...(effectiveModel ? { model: effectiveModel } : {})
				},
				callbacks: streamCallbacks,
				signal: abortController.signal,
				perChatOverrides
			});
			if (agenticResult.handled) {
				// Generate LLM based title for new conversations after agentic flow completes
				if (firstUserMessageContent) {
					await this.generateTitleWithLLM(firstUserMessageContent, streamedContent, convId);
				}
				// Check if there's a pending steering message to re-send
				const pending = agenticStore.consumePendingSteeringMessage(convId);
				if (pending) {
					await this.sendMessage(pending.content, pending.extras);
				}
				return;
			}
		}

		await ChatService.sendMessage(
			processedMessages,
			{
				...this.getApiOptions(),
				...(effectiveModel ? { model: effectiveModel } : {}),
				stream: true,
				onChunk: streamCallbacks.onChunk,
				onReasoningChunk: streamCallbacks.onReasoningChunk,
				onModel: streamCallbacks.onModel,
				onCompletionId: streamCallbacks.onCompletionId,
				onTimings: streamCallbacks.onTimings,
				onRecalledMemories: (recalled) => {
					recalledMemoriesData = recalled;
					const idx = conversationsStore.findMessageIndex(currentMessageId);
					if (idx !== -1) {
						conversationsStore.updateMessageAtIndex(idx, { recalledMemories: recalled });
					}
				},
				onConnectionState: (state: StreamConnectionState) => {
					if (convId === conversationsStore.activeConversation?.id) {
						this.streamConnectionState = state;
					}
				},
				onComplete: async (
					finalContent?: string,
					reasoningContent?: string,
					timings?: ChatMessageTimings,
					toolCalls?: string
				) => {
					const content = streamedContent || finalContent || '';
					const reasoning = streamedReasoningContent || reasoningContent;

					const updateData: Record<string, unknown> = {
						content,
						reasoningContent: reasoning || undefined,
						toolCalls: toolCalls || '',
						timings,
						recalledMemories: recalledMemoriesData
					};
					if (resolvedModel && !modelPersisted) updateData.model = resolvedModel;
					await DatabaseService.updateMessage(currentMessageId, updateData);
					const idx = conversationsStore.findMessageIndex(currentMessageId);
					const uiUpdate: Partial<DatabaseMessage> = {
						content,
						reasoningContent: reasoning || undefined,
						toolCalls: toolCalls || '',
						recalledMemories: recalledMemoriesData
					};
					if (timings) uiUpdate.timings = timings;
					if (resolvedModel) uiUpdate.model = resolvedModel;
					conversationsStore.updateMessageAtIndex(idx, uiUpdate);
					await conversationsStore.updateCurrentNode(currentMessageId);
					cleanupStreamingState();

					this.updateAIHistory().catch(console.error);

					if (onComplete) await onComplete(content);
					if (isRouterMode()) modelsStore.fetchRouterModels().catch(console.error);

					// Generate LLM based title for new conversations (avoids stale reference
					// issue when user switches conversations while streaming)
					if (firstUserMessageContent) {
						await this.generateTitleWithLLM(firstUserMessageContent, streamedContent, convId);
					}

					// Check if there's a pending message queued during streaming
					const pending = this.consumePendingMessage(convId);
					if (pending) {
						await this.sendMessage(pending.content, pending.extras);
					}
				},
				onError: streamCallbacks.onError
			},
			convId,
			abortController.signal
		);
	}

	async stopGeneration(): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv) return;
		await this.stopGenerationForChat(activeConv.id);
	}
	async stopGenerationForChat(convId: string): Promise<void> {
		await this.savePartialResponseIfNeeded(convId);
		this.setStreamingActive(false);
		// tell the server to stop the generation, not just drop the HTTP socket. without this the
		// detached drain keeps producing tokens until eos or max_tokens. use the frozen identity
		// captured when the session started, not the live dropdown
		const streamStateForStop = this.chatStreamingStates.get(convId);
		const modelForStop = streamStateForStop?.model;
		void ChatService.cancelServerStream(convId, modelForStop);
		this.abortRequest(convId);
		this.setChatLoading(convId, false);
		this.clearChatStreaming(convId);
		this.setProcessingState(convId, null);
		this.clearPendingMessage(convId);
	}

	private async generateTitleWithLLM(
		userContent: string,
		assistantContent: string,
		convId: string
	): Promise<void> {
		const effectiveModel = isRouterMode() && selectedModelName() ? selectedModelName() : undefined;
		const configValue = config();
		const titlePromptTemplate =
			typeof configValue.titleGenerationPrompt === 'string' &&
				configValue.titleGenerationPrompt.trim()
				? configValue.titleGenerationPrompt
				: TITLE_GENERATION.DEFAULT_PROMPT;

		const titlePrompt = titlePromptTemplate
			.replace('{{USER}}', String(userContent || ''))
			.replace('{{ASSISTANT}}', String(assistantContent || ''));

		const titleMessage: ApiChatMessageData = {
			role: MessageRole.USER,
			content: titlePrompt
		};

		const titleResponse = await ChatService.generateTitle(titleMessage, effectiveModel);

		if (!titleResponse) {
			return;
		}

		let cleanTitle = titleResponse.trim();
		cleanTitle = cleanTitle
			.replace(TITLE_GENERATION.PREFIX_PATTERN, '')
			.replace(TITLE_GENERATION.QUOTE_PATTERN, '')
			.trim();
		if (!cleanTitle || cleanTitle.length < TITLE_GENERATION.MIN_LENGTH) {
			const firstLine = userContent.split('\n').find((l) => l.trim().length > 0);
			cleanTitle = firstLine ? firstLine.trim() : TITLE_GENERATION.FALLBACK;
		}
		if (cleanTitle && cleanTitle.length >= TITLE_GENERATION.MIN_LENGTH) {
			await conversationsStore.updateConversationName(convId, cleanTitle);
		}
	}

	private async savePartialResponseIfNeeded(convId?: string): Promise<void> {
		const conversationId = convId || conversationsStore.activeConversation?.id;
		if (!conversationId) return;
		const streamingState = this.getChatStreaming(conversationId);
		if (!streamingState) return;
		const messages =
			conversationId === conversationsStore.activeConversation?.id
				? conversationsStore.activeMessages
				: await conversationsStore.getConversationMessages(conversationId);
		if (!messages.length) return;
		const lastMessage = messages[messages.length - 1];
		if (lastMessage?.role !== MessageRole.ASSISTANT) return;

		const partialContent = streamingState.response;
		const partialReasoning = lastMessage.reasoningContent || '';

		// nothing to persist when both content and reasoning are empty (e.g. stop before any token)
		if (!partialContent.trim() && !partialReasoning.trim()) return;

		try {
			const updateData: {
				content: string;
				reasoningContent?: string;
				timings?: ChatMessageTimings;
			} = {
				content: partialContent
			};
			if (partialReasoning) {
				updateData.reasoningContent = partialReasoning;
			}
			const lastKnownState = this.getProcessingState(conversationId);
			if (lastKnownState) {
				updateData.timings = {
					prompt_n: lastKnownState.promptTokens || 0,
					prompt_ms: lastKnownState.promptMs,
					predicted_n: lastKnownState.tokensDecoded || 0,
					cache_n: lastKnownState.cacheTokens || 0,
					predicted_ms:
						lastKnownState.tokensPerSecond && lastKnownState.tokensDecoded
							? (lastKnownState.tokensDecoded / lastKnownState.tokensPerSecond) * 1000
							: undefined
				};
			}
			await DatabaseService.updateMessage(lastMessage.id, updateData);
			lastMessage.content = partialContent;
			if (updateData.timings) lastMessage.timings = updateData.timings;
		} catch (error) {
			lastMessage.content = partialContent;
			console.error('Failed to save partial response:', error);
		}
	}

	async updateMessage(messageId: string, newContent: string): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv) return;
		if (this.isChatLoadingInternal(activeConv.id)) await this.stopGeneration();
		const result = this.getMessageByIdWithRole(messageId, MessageRole.USER);
		if (!result) return;
		const { message: messageToUpdate, index: messageIndex } = result;
		const originalContent = messageToUpdate.content;
		try {
			const allMessages = await conversationsStore.getConversationMessages(activeConv.id);
			const rootMessage = allMessages.find((m) => m.type === 'root' && m.parent === null);
			const isFirstUserMessage = rootMessage && messageToUpdate.parent === rootMessage.id;
			conversationsStore.updateMessageAtIndex(messageIndex, { content: newContent });
			await DatabaseService.updateMessage(messageId, { content: newContent });
			if (isFirstUserMessage && newContent.trim())
				await conversationsStore.updateConversationTitleWithConfirmation(
					activeConv.id,
					generateConversationTitle(newContent, Boolean(config().titleGenerationUseFirstLine))
				);
			const messagesToRemove = conversationsStore.activeMessages.slice(messageIndex + 1);
			for (const message of messagesToRemove) await DatabaseService.deleteMessage(message.id);
			conversationsStore.sliceActiveMessages(messageIndex + 1);
			conversationsStore.updateConversationTimestamp();
			this.setChatLoading(activeConv.id, true);
			this.clearChatStreaming(activeConv.id);
			const assistantMessage = await this.createAssistantMessage();
			conversationsStore.addMessageToActive(assistantMessage);
			await conversationsStore.updateCurrentNode(assistantMessage.id);
			await this.streamChatCompletion(
				conversationsStore.activeMessages.slice(0, -1),
				assistantMessage,
				undefined,
				() => {
					conversationsStore.updateMessageAtIndex(conversationsStore.findMessageIndex(messageId), {
						content: originalContent
					});
				}
			);
		} catch (error) {
			if (!isAbortError(error)) console.error('Failed to update message:', error);
		}
	}

	async regenerateMessage(messageId: string): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv || this.isChatLoadingInternal(activeConv.id)) return;
		// ⚡ Guard: TTS再生中は再生成をブロック
		if (ttsStore.isPlaying || (typeof window !== 'undefined' && ((window as any).isDeiningAudioSequenceActive || (window as any).deiningAvatarCore?.isTtsPlaying?.()))) {
			console.warn('[regenerateMessage] Blocked: TTS audio is currently playing');
			return;
		}
		this.cancelPreEncode();
		const result = this.getMessageByIdWithRole(messageId, MessageRole.ASSISTANT);
		if (!result) return;
		const { index: messageIndex } = result;
		try {
			const messagesToRemove = conversationsStore.activeMessages.slice(messageIndex);
			for (const message of messagesToRemove) await DatabaseService.deleteMessage(message.id);
			conversationsStore.sliceActiveMessages(messageIndex);
			conversationsStore.updateConversationTimestamp();
			this.setChatLoading(activeConv.id, true);
			this.clearChatStreaming(activeConv.id);
			const parentMessageId =
				conversationsStore.activeMessages.length > 0
					? conversationsStore.activeMessages[conversationsStore.activeMessages.length - 1].id
					: undefined;
			const assistantMessage = await this.createAssistantMessage(parentMessageId);
			conversationsStore.addMessageToActive(assistantMessage);
			await this.streamChatCompletion(
				conversationsStore.activeMessages.slice(0, -1),
				assistantMessage
			);
		} catch (error) {
			if (!isAbortError(error)) console.error('Failed to regenerate message:', error);
			this.setChatLoading(activeConv?.id || '', false);
		}
	}

	async regenerateMessageWithBranching(messageId: string, modelOverride?: string): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv || this.isChatLoadingInternal(activeConv.id)) return;
		// ⚡ Guard: TTS再生中は再生成をブロック
		if (ttsStore.isPlaying || (typeof window !== 'undefined' && ((window as any).isDeiningAudioSequenceActive || (window as any).deiningAvatarCore?.isTtsPlaying?.()))) {
			console.warn('[regenerateMessageWithBranching] Blocked: TTS audio is currently playing');
			return;
		}
		this.cancelPreEncode();
		try {
			const idx = conversationsStore.findMessageIndex(messageId);
			if (idx === -1) return;
			const msg = conversationsStore.activeMessages[idx];
			if (msg.role !== MessageRole.ASSISTANT) return;
			const allMessages = await conversationsStore.getConversationMessages(activeConv.id);
			const parentMessage = findMessageById(allMessages, msg.parent);
			if (!parentMessage) return;
			this.setChatLoading(activeConv.id, true);
			this.clearChatStreaming(activeConv.id);
			const newAssistantMessage = await DatabaseService.createMessageBranch(
				{
					convId: msg.convId,
					type: msg.type,
					timestamp: Date.now(),
					role: msg.role,
					content: '',
					toolCalls: '',
					children: [],
					model: null
				},
				parentMessage.id
			);
			await conversationsStore.updateCurrentNode(newAssistantMessage.id);
			conversationsStore.updateConversationTimestamp();
			await conversationsStore.refreshActiveMessages();
			const conversationPath = filterByLeafNodeId(
				allMessages,
				parentMessage.id,
				false
			) as DatabaseMessage[];
			const modelToUse = modelOverride || msg.model || undefined;
			await this.streamChatCompletion(
				conversationPath,
				newAssistantMessage,
				undefined,
				undefined,
				modelToUse
			);
		} catch (error) {
			if (!isAbortError(error))
				console.error('Failed to regenerate message with branching:', error);
			this.setChatLoading(activeConv?.id || '', false);
		}
	}

	async getDeletionInfo(messageId: string): Promise<{
		totalCount: number;
		userMessages: number;
		assistantMessages: number;
		messageTypes: string[];
	}> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv)
			return { totalCount: 0, userMessages: 0, assistantMessages: 0, messageTypes: [] };
		const allMessages = await conversationsStore.getConversationMessages(activeConv.id);
		let messageToDelete = findMessageById(allMessages, messageId);

		// Live2Dなどの非表示ユーザーメッセージに対するアシスタントの返答を削除しようとした場合：
		// 集計対象を親メッセージのIDにすり替えて、一緒に巻き込んでカウントされるようにします。
		if (messageToDelete && messageToDelete.role === MessageRole.ASSISTANT && messageToDelete.parent) {
			const parentMsg = findMessageById(allMessages, messageToDelete.parent);
			if (parentMsg && parentMsg.isHidden) {
				messageId = parentMsg.id;
				messageToDelete = parentMsg;
			}
		}

		// For system messages, don't count descendants as they will be preserved (reparented to root)
		if (messageToDelete?.role === MessageRole.SYSTEM) {
			const messagesToDelete = allMessages.filter((m) => m.id === messageId);
			let userMessages = 0,
				assistantMessages = 0;
			const messageTypes: string[] = [];

			for (const msg of messagesToDelete) {
				if (msg.role === MessageRole.USER) {
					userMessages++;
					if (!messageTypes.includes('user message')) messageTypes.push('user message');
				} else if (msg.role === MessageRole.ASSISTANT) {
					assistantMessages++;
					if (!messageTypes.includes('assistant response')) messageTypes.push('assistant response');
				}
			}

			return { totalCount: 1, userMessages, assistantMessages, messageTypes };
		}

		const descendants = findDescendantMessages(allMessages, messageId);
		const allToDelete = [messageId, ...descendants];
		const messagesToDelete = allMessages.filter((m) => allToDelete.includes(m.id));
		let userMessages = 0,
			assistantMessages = 0;
		const messageTypes: string[] = [];

		for (const msg of messagesToDelete) {
			if (msg.role === MessageRole.USER) {
				userMessages++;
				if (!messageTypes.includes('user message')) messageTypes.push('user message');
			} else if (msg.role === MessageRole.ASSISTANT) {
				assistantMessages++;
				if (!messageTypes.includes('assistant response')) messageTypes.push('assistant response');
			}
		}

		return { totalCount: allToDelete.length, userMessages, assistantMessages, messageTypes };
	}

	async deleteMessage(messageId: string): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv) return;
		try {
			const allMessages = await conversationsStore.getConversationMessages(activeConv.id);
			let messageToDelete = findMessageById(allMessages, messageId);

			if (!messageToDelete) return;

			// Live2Dなどの非表示ユーザーメッセージに対するアシスタントの返答を削除しようとした場合：
			// 元になった非表示ユーザーメッセージ(parent)も一緒に巻き込んで削除する。
			// 削除対象を親メッセージのIDにすり替えることで、カスケード削除により両方が消えます。
			if (messageToDelete.role === MessageRole.ASSISTANT && messageToDelete.parent) {
				const parentMsg = findMessageById(allMessages, messageToDelete.parent);
				if (parentMsg && parentMsg.isHidden) {
					messageId = parentMsg.id;
					messageToDelete = parentMsg;
				}
			}

			const currentPath = filterByLeafNodeId(allMessages, activeConv.currNode || '', false);
			const isInCurrentPath = currentPath.some((m) => m.id === messageId);

			if (isInCurrentPath && messageToDelete.parent) {
				const siblings = allMessages.filter(
					(m) => m.parent === messageToDelete.parent && m.id !== messageId
				);

				if (siblings.length > 0) {
					const latestSibling = siblings.reduce((latest, sibling) =>
						sibling.timestamp > latest.timestamp ? sibling : latest
					);

					await conversationsStore.updateCurrentNode(findLeafNode(allMessages, latestSibling.id));
				} else if (messageToDelete.parent) {
					await conversationsStore.updateCurrentNode(
						findLeafNode(allMessages, messageToDelete.parent)
					);
				}
			}

			await DatabaseService.deleteMessageCascading(activeConv.id, messageId);
			await conversationsStore.refreshActiveMessages();

			// 削除後の最新ノードのスナップショットで会話の要約ブロック・インデックスを再同期
			const remainingMsgs = conversationsStore.activeMessages;
			const latestMsg = remainingMsgs[remainingMsgs.length - 1];
			if (latestMsg && latestMsg.aiHistoryTextListSnapshot !== undefined) {
				const updates = {
					aiHistoryText: latestMsg.aiHistoryTextSnapshot ?? '',
					lastSummarizedIndex: latestMsg.lastSummarizedIndexSnapshot ?? 0,
					aiHistoryTextList: latestMsg.aiHistoryTextListSnapshot ?? [],
					isSlidingActive: latestMsg.isSlidingActiveSnapshot ?? false
				};
				await DatabaseService.updateConversation(activeConv.id, updates);
				conversationsStore.activeConversation = {
					...conversationsStore.activeConversation!,
					...updates
				};
			}

			conversationsStore.updateConversationTimestamp();
		} catch (error) {
			console.error('Failed to delete message:', error);
		}
	}

	/**
	 * Open a fresh assistant turn anchored at the last tool result of a resolved
	 * agentic round and let streamChatCompletion route through runAgenticFlow.
	 * Used by continueAssistantMessage when classifyContinueIntent returns
	 * next_turn, meaning the target assistant already has its tool_calls paired
	 * with trailing tool results and the next thing to generate is a brand new
	 * turn rather than a token level continuation.
	 */
	private async continueAsNextAgenticTurn(anchorIndex: number): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv) return;
		const anchor = conversationsStore.activeMessages[anchorIndex];
		if (!anchor) return;
		this.cancelPreEncode();
		this.setChatLoading(activeConv.id, true);
		this.clearChatStreaming(activeConv.id);
		try {
			const allMessages = await conversationsStore.getConversationMessages(activeConv.id);
			const anchorMessage = findMessageById(allMessages, anchor.id);
			if (!anchorMessage) {
				this.setChatLoading(activeConv.id, false);
				return;
			}
			const newAssistantMessage = await DatabaseService.createMessageBranch(
				{
					convId: activeConv.id,
					type: MessageType.TEXT,
					timestamp: Date.now(),
					role: MessageRole.ASSISTANT,
					content: '',
					toolCalls: '',
					children: [],
					model: null
				},
				anchorMessage.id
			);
			await conversationsStore.updateCurrentNode(newAssistantMessage.id);
			conversationsStore.updateConversationTimestamp();
			await conversationsStore.refreshActiveMessages();
			const conversationPath = filterByLeafNodeId(
				allMessages,
				anchorMessage.id,
				false
			) as DatabaseMessage[];
			await this.streamChatCompletion(conversationPath, newAssistantMessage);
		} catch (error) {
			if (!isAbortError(error)) console.error('Failed to continue agentic turn:', error);
			this.setChatLoading(activeConv.id, false);
		}
	}

	async continueAssistantMessage(messageId: string): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv || this.isChatLoadingInternal(activeConv.id)) return;
		const result = this.getMessageByIdWithRole(messageId, MessageRole.ASSISTANT);

		if (!result) return;

		const { message: msg, index: idx } = result;

		// Decide which resume path applies. tool_calls without tool results can
		// not be resumed mid sequence by continue_final_message, branch instead.
		// tool_calls already paired with tool results need a fresh next turn,
		// not a token level continuation of the target assistant.
		const intent = classifyContinueIntent(conversationsStore.activeMessages, idx);
		if (intent.kind === ContinueIntentKind.RERUN_TURN) {
			return this.regenerateMessageWithBranching(messageId);
		}
		if (intent.kind === ContinueIntentKind.NEXT_TURN) {
			return this.continueAsNextAgenticTurn(intent.truncateAfter);
		}

		try {
			this.showErrorDialog(null);
			this.setChatLoading(activeConv.id, true);
			this.clearChatStreaming(activeConv.id);

			const allMessages = await conversationsStore.getConversationMessages(activeConv.id);
			const dbMessage = findMessageById(allMessages, messageId);

			if (!dbMessage) {
				this.setChatLoading(activeConv.id, false);
				return;
			}

			const originalContent = dbMessage.content;
			const originalReasoning = dbMessage.reasoningContent || '';
			// Hand the persisted DatabaseMessage straight to sendMessage so its
			// internal converter preserves tool_calls and extras when present.
			// Reconstructing a bare {role, content} here would drop those fields
			// and break continue_final_message for messages with tool calls.
			const contextWithContinue = conversationsStore.activeMessages.slice(0, idx + 1);

			let appendedContent = '';
			let appendedReasoning = '';
			let hasReceivedContent = false;

			const updateStreamingContent = (fullContent: string) => {
				this.setChatStreaming(msg.convId, fullContent, msg.id);
				// resolve the row by id on every write, switching to another conv mid continue makes
				// this a no op instead of writing positionally into the now displayed conversation
				conversationsStore.updateMessageAtIndex(conversationsStore.findMessageIndex(msg.id), {
					content: fullContent
				});
			};

			const abortController = this.getOrCreateAbortController(msg.convId);

			await ChatService.sendMessage(
				contextWithContinue,
				{
					...this.getApiOptions(),
					continueFinalMessage: true,
					onConnectionState: (state: StreamConnectionState) => {
						if (msg.convId === conversationsStore.activeConversation?.id) {
							this.streamConnectionState = state;
						}
					},
					onChunk: (chunk: string) => {
						appendedContent += chunk;
						hasReceivedContent = true;
						updateStreamingContent(originalContent + appendedContent);
						this.setChatReasoning(msg.convId, false);
					},
					onCompletionId: (id: string) => {
						if (!id) return;
						// refresh the message id so a later skip targets the live slot after a continue
						conversationsStore.updateMessageAtIndex(conversationsStore.findMessageIndex(msg.id), {
							completionId: id
						});
						DatabaseService.updateMessage(msg.id, { completionId: id }).catch(() => { });
					},
					onReasoningChunk: (chunk: string) => {
						appendedReasoning += chunk;
						hasReceivedContent = true;
						// mark streaming state so a stop mid-thinking can persist the partial reasoning
						this.setChatStreaming(msg.convId, originalContent + appendedContent, msg.id);
						conversationsStore.updateMessageAtIndex(conversationsStore.findMessageIndex(msg.id), {
							reasoningContent: originalReasoning + appendedReasoning
						});
						this.setChatReasoning(msg.convId, true);
					},
					onTimings: (timings?: ChatMessageTimings, promptProgress?: ChatMessagePromptProgress) => {
						const tokensPerSecond =
							timings?.predicted_ms && timings?.predicted_n
								? (timings.predicted_n / timings.predicted_ms) * 1000
								: 0;
						this.updateProcessingStateFromTimings(
							{
								prompt_n: timings?.prompt_n || 0,
								prompt_ms: timings?.prompt_ms,
								predicted_n: timings?.predicted_n || 0,
								predicted_per_second: tokensPerSecond,
								cache_n: timings?.cache_n || 0,
								prompt_progress: promptProgress
							},
							msg.convId
						);
					},
					onComplete: async (
						finalContent?: string,
						reasoningContent?: string,
						timings?: ChatMessageTimings
					) => {
						const finalAppendedContent = hasReceivedContent ? appendedContent : finalContent || '';
						const finalAppendedReasoning = hasReceivedContent
							? appendedReasoning
							: reasoningContent || '';
						const fullContent = originalContent + finalAppendedContent;
						const fullReasoning = originalReasoning + finalAppendedReasoning || undefined;

						await DatabaseService.updateMessage(msg.id, {
							content: fullContent,
							reasoningContent: fullReasoning,
							timestamp: Date.now(),
							timings
						});

						conversationsStore.updateMessageAtIndex(conversationsStore.findMessageIndex(msg.id), {
							content: fullContent,
							reasoningContent: fullReasoning,
							timestamp: Date.now(),
							timings
						});

						conversationsStore.updateConversationTimestamp();

						this.setChatLoading(msg.convId, false);
						this.clearChatStreaming(msg.convId);
						this.setProcessingState(msg.convId, null);
					},
					onError: async (error: Error) => {
						if (isAbortError(error)) {
							if (hasReceivedContent && appendedContent) {
								await DatabaseService.updateMessage(msg.id, {
									content: originalContent + appendedContent,
									reasoningContent: originalReasoning + appendedReasoning || undefined,
									timestamp: Date.now()
								});

								conversationsStore.updateMessageAtIndex(
									conversationsStore.findMessageIndex(msg.id),
									{
										content: originalContent + appendedContent,
										reasoningContent: originalReasoning + appendedReasoning || undefined,
										timestamp: Date.now()
									}
								);
							}

							this.setChatLoading(msg.convId, false);
							this.clearChatStreaming(msg.convId);
							this.setProcessingState(msg.convId, null);

							return;
						}

						console.error('Continue generation error:', error);
						// keep whatever was appended so far, the message stays in memory and in DB
						await DatabaseService.updateMessage(msg.id, {
							content: originalContent + appendedContent,
							reasoningContent: originalReasoning + appendedReasoning || undefined,
							timestamp: Date.now()
						});
						conversationsStore.updateMessageAtIndex(conversationsStore.findMessageIndex(msg.id), {
							content: originalContent + appendedContent,
							reasoningContent: originalReasoning + appendedReasoning || undefined,
							timestamp: Date.now()
						});

						this.setChatLoading(msg.convId, false);
						this.clearChatStreaming(msg.convId);
						this.setProcessingState(msg.convId, null);
						this.showErrorDialog({
							type:
								error.name === 'TimeoutError' ? ErrorDialogType.TIMEOUT : ErrorDialogType.SERVER,
							message: error.message
						});
					}
				},

				msg.convId,
				abortController.signal
			);
		} catch (error) {
			if (!isAbortError(error)) console.error('Failed to continue message:', error);
			if (activeConv) this.setChatLoading(activeConv.id, false);
		}
	}

	async editAssistantMessage(
		messageId: string,
		newContent: string,
		shouldBranch: boolean
	): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv || this.isChatLoadingInternal(activeConv.id)) return;

		const result = this.getMessageByIdWithRole(messageId, MessageRole.ASSISTANT);
		if (!result) return;

		const { message: msg, index: idx } = result;

		try {
			if (shouldBranch) {
				const newMessage = await DatabaseService.createMessageBranch(
					{
						convId: msg.convId,
						type: msg.type,
						timestamp: Date.now(),
						role: msg.role,
						content: newContent,
						toolCalls: msg.toolCalls || '',
						children: [],
						model: msg.model
					},
					msg.parent!
				);

				await conversationsStore.updateCurrentNode(newMessage.id);
			} else {
				await DatabaseService.updateMessage(msg.id, { content: newContent });
				conversationsStore.updateMessageAtIndex(idx, { content: newContent });
			}

			conversationsStore.updateConversationTimestamp();

			await conversationsStore.refreshActiveMessages();
		} catch (error) {
			console.error('Failed to edit assistant message:', error);
		}
	}

	async editUserMessagePreserveResponses(
		messageId: string,
		newContent: string,
		newExtras?: DatabaseMessageExtra[]
	): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv) return;

		const result = this.getMessageByIdWithRole(messageId, MessageRole.USER);
		if (!result) return;

		const { message: msg, index: idx } = result;
		try {
			const updateData: Partial<DatabaseMessage> = { content: newContent };

			if (newExtras !== undefined) updateData.extra = JSON.parse(JSON.stringify(newExtras));

			await DatabaseService.updateMessage(messageId, updateData);

			conversationsStore.updateMessageAtIndex(idx, updateData);

			const allMessages = await conversationsStore.getConversationMessages(activeConv.id);
			const rootMessage = allMessages.find((m) => m.type === 'root' && m.parent === null);

			if (rootMessage && msg.parent === rootMessage.id && newContent.trim()) {
				await conversationsStore.updateConversationTitleWithConfirmation(
					activeConv.id,
					generateConversationTitle(newContent, Boolean(config().titleGenerationUseFirstLine))
				);
			}

			conversationsStore.updateConversationTimestamp();
		} catch (error) {
			console.error('Failed to edit user message:', error);
		}
	}

	async editMessageWithBranching(
		messageId: string,
		newContent: string,
		newExtras?: DatabaseMessageExtra[]
	): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv || this.isChatLoadingInternal(activeConv.id)) return;
		let result = this.getMessageByIdWithRole(messageId, MessageRole.USER);
		if (!result) result = this.getMessageByIdWithRole(messageId, MessageRole.SYSTEM);
		if (!result) return;
		const { message: msg, index: idx } = result;
		try {
			const allMessages = await conversationsStore.getConversationMessages(activeConv.id);
			const rootMessage = allMessages.find((m) => m.type === 'root' && m.parent === null);
			const isFirstUserMessage =
				msg.role === MessageRole.USER && rootMessage && msg.parent === rootMessage.id;
			const extrasToUse =
				newExtras !== undefined
					? JSON.parse(JSON.stringify(newExtras))
					: msg.extra
						? JSON.parse(JSON.stringify(msg.extra))
						: undefined;

			let messageIdForResponse: string;

			const dbMsg = findMessageById(allMessages, msg.id);
			const hasChildren = dbMsg ? dbMsg.children.length > 0 : msg.children.length > 0;

			if (!hasChildren) {
				// No responses after this message — update in place instead of branching
				const updates: Partial<DatabaseMessage> = {
					content: newContent,
					timestamp: Date.now(),
					extra: extrasToUse
				};
				await DatabaseService.updateMessage(msg.id, updates);
				conversationsStore.updateMessageAtIndex(idx, updates);
				messageIdForResponse = msg.id;
			} else {
				// Has children — create a new branch as sibling
				const parentId = msg.parent || rootMessage?.id;
				if (!parentId) return;
				const newMessage = await DatabaseService.createMessageBranch(
					{
						convId: msg.convId,
						type: msg.type,
						timestamp: Date.now(),
						role: msg.role,
						content: newContent,
						toolCalls: msg.toolCalls || '',
						children: [],
						extra: extrasToUse,
						model: msg.model
					},
					parentId
				);
				await conversationsStore.updateCurrentNode(newMessage.id);
				messageIdForResponse = newMessage.id;
			}

			conversationsStore.updateConversationTimestamp();
			if (isFirstUserMessage && newContent.trim())
				await conversationsStore.updateConversationTitleWithConfirmation(
					activeConv.id,
					generateConversationTitle(newContent, Boolean(config().titleGenerationUseFirstLine))
				);
			await conversationsStore.refreshActiveMessages();
			if (msg.role === MessageRole.USER)
				await this.generateResponseForMessage(messageIdForResponse);
		} catch (error) {
			console.error('Failed to edit message with branching:', error);
		}
	}

	private async generateResponseForMessage(userMessageId: string): Promise<void> {
		const activeConv = conversationsStore.activeConversation;
		if (!activeConv) return;

		this.showErrorDialog(null);
		this.setChatLoading(activeConv.id, true);
		this.clearChatStreaming(activeConv.id);

		try {
			const allMessages = await conversationsStore.getConversationMessages(activeConv.id);
			const conversationPath = filterByLeafNodeId(
				allMessages,
				userMessageId,
				false
			) as DatabaseMessage[];
			const assistantMessage = await DatabaseService.createMessageBranch(
				{
					convId: activeConv.id,
					type: MessageType.TEXT,
					timestamp: Date.now(),
					role: MessageRole.ASSISTANT,
					content: '',
					toolCalls: '',
					children: [],
					model: null
				},
				userMessageId
			);

			conversationsStore.addMessageToActive(assistantMessage);

			await this.streamChatCompletion(conversationPath, assistantMessage);
		} catch (error) {
			console.error('Failed to generate response:', error);
			this.setChatLoading(activeConv.id, false);
		}
	}

	private getContextTotal(): number | null {
		const activeConvId = this.activeConversationId;
		const activeState = activeConvId ? this.getProcessingState(activeConvId) : null;

		if (activeState && typeof activeState.contextTotal === 'number' && activeState.contextTotal > 0)
			return activeState.contextTotal;

		if (isRouterMode()) {
			const modelContextSize = selectedModelContextSize();

			if (typeof modelContextSize === 'number' && modelContextSize > 0) {
				return modelContextSize;
			}
		} else {
			const propsContextSize = contextSize();

			if (typeof propsContextSize === 'number' && propsContextSize > 0) {
				return propsContextSize;
			}
		}

		return null;
	}

	updateProcessingStateFromTimings(
		timingData: {
			prompt_n: number;
			prompt_ms?: number;
			predicted_n: number;
			predicted_per_second: number;
			cache_n: number;
			prompt_progress?: ChatMessagePromptProgress;
		},
		conversationId?: string
	): void {
		const processingState = this.parseTimingData(timingData);

		if (processingState === null) {
			console.warn('Failed to parse timing data - skipping update');
			return;
		}

		const targetId = conversationId || this.activeConversationId;
		if (targetId) {
			this.setProcessingState(targetId, processingState);
		}
	}

	private parseTimingData(timingData: Record<string, unknown>): ApiProcessingState | null {
		const promptTokens = (timingData.prompt_n as number) || 0,
			promptMs = (timingData.prompt_ms as number) || undefined,
			predictedTokens = (timingData.predicted_n as number) || 0,
			tokensPerSecond = (timingData.predicted_per_second as number) || 0,
			cacheTokens = (timingData.cache_n as number) || 0;
		const promptProgress = timingData.prompt_progress as
			| { total: number; cache: number; processed: number; time_ms: number }
			| undefined;
		const contextTotal = this.getContextTotal();
		const currentConfig = config();
		const outputTokensMax = currentConfig.max_tokens || -1;
		const contextUsed = promptTokens + cacheTokens + predictedTokens,
			outputTokensUsed = predictedTokens;
		const progressCache = promptProgress?.cache || 0,
			progressActualDone = (promptProgress?.processed ?? 0) - progressCache,
			progressActualTotal = (promptProgress?.total ?? 0) - progressCache;
		const progressPercent = promptProgress
			? Math.round((progressActualDone / progressActualTotal) * 100)
			: undefined;
		return {
			status: predictedTokens > 0 ? 'generating' : promptProgress ? 'preparing' : 'idle',
			tokensDecoded: predictedTokens,
			tokensRemaining: outputTokensMax - predictedTokens,
			contextUsed,
			contextTotal,
			outputTokensUsed,
			outputTokensMax,
			hasNextToken: predictedTokens > 0,
			tokensPerSecond,
			temperature: currentConfig.temperature ?? 0.8,
			topP: currentConfig.top_p ?? 0.95,
			speculative: false,
			progressPercent,
			promptProgress,
			promptTokens,
			promptMs,
			cacheTokens
		};
	}

	restoreProcessingStateFromMessages(messages: DatabaseMessage[], conversationId: string): void {
		for (let i = messages.length - 1; i >= 0; i--) {
			const message = messages[i];
			if (message.role === MessageRole.ASSISTANT && message.timings) {
				const restoredState = this.parseTimingData({
					prompt_n: message.timings.prompt_n || 0,
					prompt_ms: message.timings.prompt_ms,
					predicted_n: message.timings.predicted_n || 0,
					predicted_per_second:
						message.timings.predicted_n && message.timings.predicted_ms
							? (message.timings.predicted_n / message.timings.predicted_ms) * 1000
							: 0,
					cache_n: message.timings.cache_n || 0
				});
				if (restoredState) {
					this.setProcessingState(conversationId, restoredState);
					return;
				}
			}
		}
	}

	getConversationModel(messages: DatabaseMessage[]): string | null {
		for (let i = messages.length - 1; i >= 0; i--) {
			const message = messages[i];
			if (message.role === MessageRole.ASSISTANT && message.model) return message.model;
		}
		return null;
	}

	private getApiOptions(): Record<string, unknown> {
		const currentConfig = config();
		const hasValue = (value: unknown): boolean =>
			value !== undefined && value !== null && value !== '';
		const apiOptions: Record<string, unknown> = { stream: true, timings_per_token: true };

		if (isRouterMode()) {
			const modelName = selectedModelName();
			if (modelName) apiOptions.model = modelName;
		}

		if (currentConfig.systemMessage) apiOptions.systemMessage = currentConfig.systemMessage;

		if (currentConfig.disableReasoningParsing) apiOptions.disableReasoningParsing = true;

		if (currentConfig.excludeReasoningFromContext) apiOptions.excludeReasoningFromContext = true;

		apiOptions.enableThinking = conversationsStore.getThinkingEnabled();
		apiOptions.reasoningEffort = conversationsStore.getReasoningEffort();

		if (hasValue(currentConfig.temperature))
			apiOptions.temperature = Number(currentConfig.temperature);

		if (hasValue(currentConfig.max_tokens))
			apiOptions.max_tokens = Number(currentConfig.max_tokens);

		if (hasValue(currentConfig.dynatemp_range))
			apiOptions.dynatemp_range = Number(currentConfig.dynatemp_range);

		if (hasValue(currentConfig.dynatemp_exponent))
			apiOptions.dynatemp_exponent = Number(currentConfig.dynatemp_exponent);

		if (hasValue(currentConfig.top_k)) apiOptions.top_k = Number(currentConfig.top_k);

		if (hasValue(currentConfig.top_p)) apiOptions.top_p = Number(currentConfig.top_p);

		if (hasValue(currentConfig.min_p)) apiOptions.min_p = Number(currentConfig.min_p);

		if (hasValue(currentConfig.xtc_probability))
			apiOptions.xtc_probability = Number(currentConfig.xtc_probability);

		if (hasValue(currentConfig.xtc_threshold))
			apiOptions.xtc_threshold = Number(currentConfig.xtc_threshold);

		if (hasValue(currentConfig.typ_p)) apiOptions.typ_p = Number(currentConfig.typ_p);

		if (hasValue(currentConfig.repeat_last_n))
			apiOptions.repeat_last_n = Number(currentConfig.repeat_last_n);

		if (hasValue(currentConfig.repeat_penalty))
			apiOptions.repeat_penalty = Number(currentConfig.repeat_penalty);

		if (hasValue(currentConfig.presence_penalty))
			apiOptions.presence_penalty = Number(currentConfig.presence_penalty);

		if (hasValue(currentConfig.frequency_penalty))
			apiOptions.frequency_penalty = Number(currentConfig.frequency_penalty);

		if (hasValue(currentConfig.dry_multiplier))
			apiOptions.dry_multiplier = Number(currentConfig.dry_multiplier);

		if (hasValue(currentConfig.dry_base)) apiOptions.dry_base = Number(currentConfig.dry_base);

		if (hasValue(currentConfig.dry_allowed_length))
			apiOptions.dry_allowed_length = Number(currentConfig.dry_allowed_length);

		if (hasValue(currentConfig.dry_penalty_last_n))
			apiOptions.dry_penalty_last_n = Number(currentConfig.dry_penalty_last_n);

		if (currentConfig.samplers) apiOptions.samplers = currentConfig.samplers;

		apiOptions.backend_sampling =
			currentConfig.backend_sampling === true || currentConfig.backend_sampling === 'true';

		if (currentConfig.customJson) apiOptions.custom = currentConfig.customJson;

		return apiOptions;
	}

	private cancelPreEncode(): void {
		if (this.preEncodeAbortController) {
			this.preEncodeAbortController.abort();
			this.preEncodeAbortController = null;
		}
	}

	private async triggerPreEncode(
		allMessages: DatabaseMessage[],
		assistantMessage: DatabaseMessage,
		assistantContent: string,
		model?: string | null,
		excludeReasoning?: boolean
	): Promise<void> {
		return;
		this.cancelPreEncode();
		this.preEncodeAbortController = new AbortController();

		const signal = this.preEncodeAbortController.signal;

		try {
			const allIdle = await ChatService.areAllSlotsIdle(model, signal);
			if (!allIdle || signal.aborted) return;

			const messagesWithAssistant: DatabaseMessage[] = [
				...allMessages,
				{ ...assistantMessage, content: assistantContent }
			];

			await ChatService.preEncode(messagesWithAssistant, model, excludeReasoning, signal);
		} catch (err) {
			if (!isAbortError(err)) {
				console.warn('[ChatStore] Pre-encode failed:', err);
			}
		}
	}

	async rebuildConversationMemory(convId: string, forceRebuild = false): Promise<void> {
		console.log(`[Consolidation] Starting memory rebuild for conversation ${convId} (forceRebuild=${forceRebuild})`);

		const conversation = await DatabaseService.getConversation(convId);
		if (!conversation) {
			console.error(`[Consolidation] Conversation ${convId} not found`);
			return;
		}

		// すでに要約が存在し、強制再生成フラグがオフなら早期リターン
		const existingList = conversation.aiHistoryTextList ?? [];
		if (!forceRebuild && existingList.length > 0) {
			console.log(`[Consolidation] Summaries already exist for ${convId}, skipping rebuild.`);
			return;
		}

		this.rebuildingConversationId = convId;

		const messages = await DatabaseService.getConversationMessages(convId);
		if (!messages || messages.length === 0) {
			console.log(`[Consolidation] No messages in conversation ${convId}`);
			this.rebuildingConversationId = null;
			return;
		}

		const safeToast = {
			loading: (msg: string, opts?: any) => (typeof toast !== 'undefined' && toast?.loading ? toast.loading(msg, opts) : undefined),
			success: (msg: string, opts?: any) => (typeof toast !== 'undefined' && toast?.success ? toast.success(msg, opts) : undefined),
			error: (msg: string, opts?: any) => (typeof toast !== 'undefined' && toast?.error ? toast.error(msg, opts) : undefined),
			dismiss: (id?: any) => (typeof toast !== 'undefined' && toast?.dismiss ? toast.dismiss(id) : undefined)
		};

		// トーストを早期に表示してエラー発生時にも追跡できるようにする
		const toastId = safeToast.loading(t('toast.rebuildingMemory', 'Rebuilding chat memories...'), { duration: Infinity });

		// 強制再生成の場合、DBの要約データおよびメッセージのスナップショットをリセット
		// ※ try の外で実行し、クリア処理自体がエラーで妨げられないようにする
		if (forceRebuild) {
			console.log(`[Consolidation] forceRebuild is active. Resetting existing summaries & RAG memories for ${convId}`);
			try {
				await DatabaseService.updateConversation(convId, {
					aiHistoryTextList: [],
					isSlidingActive: false,
					lastSummarizedIndex: 0,
					aiHistoryText: '',
					archivedSummaries: []
				});
				console.log(`[Consolidation] Conversation summary data cleared for ${convId}`);
			} catch (clearError) {
				console.error(`[Consolidation] Failed to clear conversation summary data:`, clearError);
			}

			// ChromaDB 上の既存の事実記憶 (RAG) も一旦クリアして一から再構築させる
			try {
				let cleared = false;
				const hosts = [
					(typeof window !== 'undefined' && window.location.hostname) ? window.location.hostname : 'localhost',
					'127.0.0.1',
					'localhost'
				];
				for (const h of new Set(hosts)) {
					try {
						const res = await fetch(`http://${h}:5000/memory/clear`, {
							method: 'POST',
							headers: { 'Content-Type': 'application/json' },
							body: JSON.stringify({ session_id: convId })
						});
						if (res.ok) { cleared = true; break; }
					} catch (e) {
						// ignore connection errors to retry next host
					}
				}
				if (cleared) {
					console.log(`[Consolidation] ChromaDB RAG memories cleared for ${convId}`);
					if (typeof window !== 'undefined') {
						window.dispatchEvent(new CustomEvent('memoryUpdated'));
					}
				} else {
					console.warn(`[Consolidation] Could not reach ChromaDB clear endpoint for ${convId}`);
				}
			} catch (ragClearError) {
				console.warn(`[Consolidation] Failed to clear ChromaDB RAG memories for ${convId}:`, ragClearError);
			}

			// メモリ側の activeConversation も同期
			if (conversationsStore.activeConversation?.id === convId) {
				conversationsStore.activeConversation = {
					...conversationsStore.activeConversation,
					aiHistoryTextList: [],
					isSlidingActive: false,
					lastSummarizedIndex: 0,
					aiHistoryText: '',
					archivedSummaries: []
				};
			}

			// メッセージのスナップショットをクリア（個別エラーは無視して続行）
			for (const m of messages) {
				if (
					m.aiHistoryTextListSnapshot !== undefined ||
					m.lastSummarizedIndexSnapshot !== undefined ||
					m.isSlidingActiveSnapshot !== undefined ||
					m.aiHistoryTextSnapshot !== undefined
				) {
					try {
						const updated = { ...m };
						delete updated.aiHistoryTextListSnapshot;
						delete updated.isSlidingActiveSnapshot;
						delete updated.lastSummarizedIndexSnapshot;
						delete updated.aiHistoryTextSnapshot;
						await DatabaseService.putMessage(updated);
					} catch (msgError) {
						console.warn(`[Consolidation] Failed to clear snapshot for message ${m.id}:`, msgError);
					}
				}
			}
		}

		try {
			let currNodeId = conversation.currNode;
			if (!currNodeId) {
				const lastMsg = messages[messages.length - 1];
				if (lastMsg) {
					currNodeId = lastMsg.id;
					await DatabaseService.updateConversation(convId, { currNode: currNodeId });
					console.log(`[Consolidation] Automatically set currNode to ${currNodeId} for conversation ${convId}`);
				} else {
					console.log(`[Consolidation] No currNode or messages in conversation ${convId}`);
					safeToast.dismiss(toastId);
					this.rebuildingConversationId = null;
					return;
				}
			}

			const messageMap = new Map<string, DatabaseMessage>();
			for (const m of messages) {
				messageMap.set(m.id, m);
			}

			const path: DatabaseMessage[] = [];
			let currentId = currNodeId;
			while (currentId) {
				const m = messageMap.get(currentId);
				if (!m) break;
				path.unshift(m);
				currentId = m.parent ?? '';
			}

			// parentリンクが存在しないフラットなインポートデータの場合、全メッセージを path とする
			if (path.length === 0) {
				path.push(...messages);
			}

			let targetMessages = path.filter(m => m.type !== 'root' && String(m.role).toLowerCase() !== 'system' && !m.isHidden);

			// ツリー構造の parent リンクが途切れていて targetMessages が 0 件になった場合、
			// 会話内の全 messages から直接チャットメッセージを抽出するフォールバック
			if (targetMessages.length === 0 && messages.length > 0) {
				console.log(`[Consolidation] Path trace yielded 0 chat messages. Falling back to all conversation messages for ${convId}`);
				targetMessages = messages.filter(m => m.type !== 'root' && String(m.role).toLowerCase() !== 'system' && !m.isHidden);
			}

			if (targetMessages.length === 0) {
				console.log(`[Consolidation] No chat messages to rebuild for ${convId}`);
				safeToast.dismiss(toastId);
				this.rebuildingConversationId = null;
				return;
			}

			const blockSize = 30;
			const blocks: DatabaseMessage[][] = [];
			for (let i = 0; i < targetMessages.length; i += blockSize) {
				blocks.push(targetMessages.slice(i, i + blockSize));
			}

			const totalBlocks = blocks.length;
			console.log(`[Consolidation] Split into ${totalBlocks} blocks`);

			safeToast.loading(t('toast.rebuildingMemoryProgress', { current: 0, total: totalBlocks }, `Rebuilding chat memories... (0 / ${totalBlocks})`), { id: toastId, duration: Infinity });

			let currentList: string[] = [];
			let isSlidingActive = false;

			const currentConfig = config();
			const summaryModel = currentConfig.summaryModel?.toString().trim() || 'main';
			const isSummaryModelMain = !summaryModel || summaryModel === 'main';
			const sumCfg = getSummaryConfig();

			for (let blockIdx = 0; blockIdx < totalBlocks; blockIdx++) {
				const blockMessages = blocks[blockIdx];
				const blockLastMessage = blockMessages[blockMessages.length - 1];
				const pathEndIdx = targetMessages.findIndex(m => m.id === blockLastMessage.id) + 1;

				let logText = '';
				for (const msg of blockMessages) {
					let content = msg.content;
					content = content.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
					if (!content) content = '(空のメッセージ)';

					let imageNote = '';
					if (msg.extra?.attachments && msg.extra.attachments.some(a => a.type === AttachmentType.IMAGE || a.mimeType?.startsWith('image/'))) {
						const imageNames = msg.extra.attachments
							.filter(a => a.type === AttachmentType.IMAGE || a.mimeType?.startsWith('image/'))
							.map(a => a.name || '画像')
							.join(', ');
						imageNote = ` [送信画像: ${imageNames}]`;
					}

					logText += `${msg.role === MessageRole.USER ? 'ユーザー' : 'AI'}: ${content}${imageNote}\n`;
				}

				// --- 1. 事実抽出 (RAG) の実行と ChromaDB 登録 ---
				try {
					const factPrompt = `## 会話ログ\n${logText}`;

					let factText = '';
					if (isSummaryModelMain) {
						const response = await fetch(API_CHAT.COMPLETIONS, {
							method: 'POST',
							headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
							body: JSON.stringify({
								model: selectedModelName() || undefined,
								messages: [
									{ role: 'system', content: sumCfg.factPrompt },
									{ role: 'user', content: factPrompt }
								],
								stream: false,
								temperature: sumCfg.factTemp,
								max_tokens: sumCfg.factMaxTokens,
								chat_template_kwargs: { enable_thinking: false }
							})
						});
						console.log(`[Consolidation] Fact extraction response status: ${response.status}`);
						if (response.ok) {
							const resData = await response.json();
							console.log(`[Consolidation] Fact extraction resData keys:`, Object.keys(resData));
							console.log(`[Consolidation] Full first choice:`, JSON.stringify(resData.choices?.[0]).substring(0, 800));
							console.log(`[Consolidation] logText length:`, logText.length, 'first 200 chars:', logText.substring(0, 200));
							const msgObj = resData.choices?.[0]?.message;
							const rawContent = msgObj?.content || msgObj?.reasoning_content || '';
							console.log(`[Consolidation] Fact extraction rawContent (first 300 chars):`, rawContent.substring(0, 300));
							factText = rawContent.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
							console.log(`[Consolidation] Fact extraction factText after <think> removal (first 300 chars):`, factText.substring(0, 300));
						} else {
							console.error(`[Consolidation] Fact extraction failed: ${response.status} ${response.statusText}`);
						}
					} else {
						const response = await fetch('http://localhost:11434/api/chat', {
							method: 'POST',
							headers: { 'Content-Type': 'application/json' },
							body: JSON.stringify({
								model: summaryModel,
								messages: [
									{ role: 'system', content: sumCfg.factPrompt },
									{ role: 'user', content: factPrompt }
								],
								stream: false,
								options: { temperature: sumCfg.factTemp, num_predict: sumCfg.factMaxTokens }
							})
						});
						console.log(`[Consolidation] Fact extraction (Ollama) response status: ${response.status}`);
						if (response.ok) {
							const resData = await response.json();
							const rawContent = resData.message?.content || '';
							console.log(`[Consolidation] Fact extraction rawContent (first 300 chars):`, rawContent.substring(0, 300));
							factText = rawContent.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
							console.log(`[Consolidation] Fact extraction factText after <think> removal (first 300 chars):`, factText.substring(0, 300));
						} else {
							console.error(`[Consolidation] Fact extraction (Ollama) failed: ${response.status} ${response.statusText}`);
						}
					}

					if (factText) {
						console.log(`[Consolidation] Raw factText for block ${blockIdx}:`, factText.substring(0, 500));
						const facts = factText.split('\n')
							.map(line => line.trim())
							.filter(line => line.length > 0)
							.map(line => line.replace(/^[\-\*・●▪▸►◆■□☆★]\s*/, '').trim())
							.map(line => line.replace(/^\d+[\.\)）]\s*/, '').trim())
							.filter(line => {
								if (line.length < 3) return false;
								const normalized = line.replace(/[\.。、，\,]/g, '').trim();
								const noSpace = normalized.replace(/\s+/g, '');
								return !(
									noSpace === 'なし' || noSpace === '無し' ||
									noSpace === '特になし' || noSpace === '特に無し' ||
									noSpace === '特にありません' || noSpace === 'ありません' ||
									noSpace === '該当なし' || noSpace === '該当無し'
								);
							});
						console.log(`[Consolidation] Parsed facts for block ${blockIdx} (${facts.length} items):`, facts);
						if (facts.length > 0) {
							const blockMsgId = blockLastMessage?.id || '';
							console.log(`🧠 [RAG Memory Saving for ${convId}]: ${facts.length} facts (msgId: ${blockMsgId})`);
							try {
								const addResp = await fetch(`http://localhost:5000/memory/add?session_id=${convId}${blockMsgId ? `&message_id=${blockMsgId}` : ''}`, {
									method: 'POST',
									headers: { 'Content-Type': 'application/json' },
									body: JSON.stringify({
										texts: facts,
										message_id: blockMsgId,
										active_message_ids: targetMessages.map(m => m.id)
									})
								});
								console.log(`🧠 [RAG Memory Save Response]: ${addResp.status} ${addResp.statusText}`);
								if (typeof window !== 'undefined') {
									window.dispatchEvent(new CustomEvent('memoryUpdated'));
								}
							} catch (err) {
								console.error('[Consolidation] Failed to save facts to ChromaDB:', err);
							}
						} else {
							console.log(`[Consolidation] No valid facts after filtering for block ${blockIdx}`);
						}
					} else {
						console.log(`[Consolidation] factText was empty for block ${blockIdx}`);
					}
				} catch (factErr) {
					console.error('[Consolidation] Error during fact extraction for block:', blockIdx, factErr);
				}

				// --- 2. あらすじ要約の実行 ---
				const prevSummary = currentList.length > 0
					? `\n## Previous Context (Summary Background)\n${currentList.map((s, idx) => `[Episode ${idx + 1}]: ${s}`).join('\n\n')}\n`
					: `\nNo previous summary available.`;

				const fullPrompt = `${prevSummary}\n## New Conversation Log\n${logText}\n## Output (Provide only the summary text matching the conversation language):`;

				let summaryText = '';
				let attempts = 0;
				const maxAttempts = 2;

				while (!summaryText && attempts < maxAttempts) {
					attempts++;
					try {
						if (isSummaryModelMain) {
							const response = await fetch(API_CHAT.COMPLETIONS, {
								method: 'POST',
								headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
								body: JSON.stringify({
									model: selectedModelName() || undefined,
									messages: [
										{ role: 'system', content: sumCfg.summaryPrompt },
										{ role: 'user', content: fullPrompt }
									],
									stream: false,
									temperature: attempts > 1 ? 0.7 : sumCfg.summaryTemp,
									max_tokens: sumCfg.summaryMaxTokens,
									chat_template_kwargs: { enable_thinking: false }
								})
							});
							if (response.ok) {
								const resData = await response.json();
								const rawContent = resData.choices?.[0]?.message?.content || '';
								summaryText = rawContent.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
							} else {
								console.warn(`[Consolidation] Failed to generate summary using main model (Attempt ${attempts}):`, response.statusText);
							}
						} else {
							const response = await fetch('http://localhost:11434/api/chat', {
								method: 'POST',
								headers: { 'Content-Type': 'application/json' },
								body: JSON.stringify({
									model: summaryModel,
									messages: [
										{ role: 'system', content: sumCfg.summaryPrompt },
										{ role: 'user', content: fullPrompt }
									],
									stream: false,
									options: { temperature: attempts > 1 ? 0.7 : sumCfg.summaryTemp, num_predict: sumCfg.summaryMaxTokens }
								})
							});
							if (response.ok) {
								const resData = await response.json();
								const rawContent = resData.message?.content || '';
								summaryText = rawContent.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
							} else {
								console.warn(`[Consolidation] Failed to generate summary using Ollama model (Attempt ${attempts}):`, response.statusText);
							}
						}
					} catch (sumErr) {
						console.error(`[Consolidation] Error during summary generation for block ${blockIdx} (Attempt ${attempts}):`, sumErr);
					}
				}

				// リトライ後も空だった場合のフォールバック（要約ブロック抜け落ち防止）
				if (!summaryText) {
					console.warn(`[Consolidation] Summary empty for block ${blockIdx}. Using fallback text to prevent block gap.`);
					const firstMsgContent = blockMessages[0]?.content?.replace(/<think>[\s\S]*?<\/think>/gi, '').trim().slice(0, 40) || '';
					const lastMsgContent = blockMessages[blockMessages.length - 1]?.content?.replace(/<think>[\s\S]*?<\/think>/gi, '').trim().slice(0, 40) || '';
					summaryText = `【エピソード整理】ユーザーとAIの会話展開（"${firstMsgContent}..." 〜 "${lastMsgContent}..." に関するやり取り）。`;
				}

				// --- 3. 忘却制御と DB 保存 ---
				if (summaryText) {
					currentList.push(summaryText);
					if (currentList.length >= MAX_SUMMARY_BLOCKS) {
						const forgottenBlocks: string[] = [];
						while (currentList.length > (MAX_SUMMARY_BLOCKS - FORGET_BLOCKS_COUNT)) {
							const forgotten = currentList.shift();
							if (forgotten) {
								forgottenBlocks.push(forgotten);
							}
						}
						isSlidingActive = true;

						if (forgottenBlocks.length > 0) {
							for (const blockText of forgottenBlocks) {
								this.archiveForgottenBlockToRag(blockText, convId, blockLastMessage?.id || '');
							}
						}
					}
				}

				const dbUpdates: Record<string, any> = {
					aiHistoryTextList: currentList,
					isSlidingActive: isSlidingActive,
					lastSummarizedIndex: pathEndIdx
				};
				if (summaryText) {
					dbUpdates.aiHistoryText = summaryText;
				}

				await DatabaseService.updateConversation(convId, dbUpdates);

				if (conversationsStore.activeConversation?.id === convId) {
					conversationsStore.activeConversation = {
						...conversationsStore.activeConversation,
						...dbUpdates
					};
				}

				// ブロック内のすべてのメッセージに、その時点の要約スナップショットを反映
				for (const msg of blockMessages) {
					const msgUpdates: Record<string, any> = {
						lastSummarizedIndexSnapshot: pathEndIdx,
						aiHistoryTextListSnapshot: currentList,
						isSlidingActiveSnapshot: isSlidingActive
					};
					if (summaryText && msg.id === blockLastMessage?.id) {
						msgUpdates.aiHistoryTextSnapshot = summaryText;
					}
					await DatabaseService.updateMessage(msg.id, msgUpdates);
				}

				safeToast.loading(t('toast.rebuildingMemoryProgress', { current: blockIdx + 1, total: totalBlocks }, `Rebuilding chat memories... (${blockIdx + 1} / ${totalBlocks})`), { id: toastId, duration: Infinity });
			}

			// 全ブロック完了後、path 上の未設定メッセージ（末尾のあまり等）にも最新スナップショットを適用
			for (const msg of path) {
				const updatedMsg = await DatabaseService.getMessage(msg.id);
				if (updatedMsg && updatedMsg.aiHistoryTextListSnapshot === undefined) {
					await DatabaseService.updateMessage(msg.id, {
						lastSummarizedIndexSnapshot: targetMessages.length,
						aiHistoryTextListSnapshot: currentList,
						isSlidingActiveSnapshot: isSlidingActive
					});
				}
			}

			safeToast.success(t('toast.rebuildSuccess', 'Chat memories and summary rebuilt successfully!'), { id: toastId });
			this.rebuildingConversationId = null;
			if (typeof window !== 'undefined') {
				window.dispatchEvent(new CustomEvent('memoryUpdated'));
			}

			// 完了後、activeConversation を DB から再取得して最新の要約データを反映
			if (conversationsStore.activeConversation?.id === convId) {
				const updatedConv = await DatabaseService.getConversation(convId);
				if (updatedConv) {
					conversationsStore.activeConversation = updatedConv;
				}
				await conversationsStore.updateCurrentNode(currNodeId);
			}

		} catch (error) {
			console.error('[Consolidation] Rebuild batch error:', error);
			safeToast.error(t('toast.rebuildError', 'An error occurred while rebuilding memories.'), { id: toastId });
			this.rebuildingConversationId = null;
		}
	}
}

export const chatStore = new ChatStore();

export const activeProcessingState = () => chatStore.activeProcessingState;
export const currentResponse = () => chatStore.currentResponse;
export const errorDialog = () => chatStore.errorDialogState;
export const getAddFilesHandler = () => chatStore.getAddFilesHandler();
export const getAllLoadingChats = () => chatStore.getAllLoadingChats();
export const getAllStreamingChats = () => chatStore.getAllStreamingChats();
export const getChatStreaming = (convId: string) => chatStore.getChatStreamingPublic(convId);
export const isChatLoading = (convId: string) => chatStore.isChatLoadingPublic(convId);
export const isChatStreaming = () => chatStore.isStreaming();
export const isEditing = () => chatStore.isEditing();
export const isLoading = () => chatStore.isLoading;
export const isReasoning = () => chatStore.isReasoning;
export const pendingEditMessageId = () => chatStore.pendingEditMessageId;
export const chatHasPendingMessage = (convId: string) => chatStore.hasPendingMessage(convId);
export const chatPendingMessageContent = (convId: string) =>
	chatStore.pendingMessageContent(convId);
export const chatPendingMessageExtras = (convId: string) => chatStore.pendingMessageExtras(convId);
export const chatClearPendingMessage = (convId: string) => chatStore.clearPendingMessage(convId);
export const chatInjectPendingMessage = (
	convId: string,
	content: string,
	extras?: DatabaseMessageExtra[]
) => chatStore.injectPendingMessage(convId, content, extras);

export const canSendChatMessage = () => {
	if (typeof window === 'undefined') return false;
	// 1. LLM生成中 / ストリーミング中 / ローディング中
	if (chatStore.isStreaming() || chatStore.isLoading) return false;
	// 2. 要約実行中
	if (chatStore.isSummarizing) return false;
	// 3. 編集モード中
	if (chatStore.isEditing()) return false;
	// 4. TTS再生中 (Svelte store)
	if (ttsStore.isPlaying) return false;
	// 5. グローバルTTS再生中 / 音声キュー待機中 (avatar-core / window)
	const win = window as any;
	if (win.deiningAvatarCore && typeof win.deiningAvatarCore.isTtsPlaying === 'function' && win.deiningAvatarCore.isTtsPlaying()) {
		return false;
	}
	if (win.isDeiningAudioSequenceActive) return false;
	return true;
};

// 泥濘統合: Live2Dアバター等からの隠しメッセージ送信API
if (typeof window !== 'undefined') {
	(window as any).canSendChatMessage = canSendChatMessage;
	(window as any).getIsGenerating = () => !canSendChatMessage();

	(window as any).sendChatMessage = async (text: string, isHidden: boolean = false) => {
		// 送信可能状態かチェック (TTS再生中やLLM生成中はブロック)
		if (!canSendChatMessage()) {
			console.warn('[sendChatMessage] Blocked: Busy state (LLM generating, summarizing, or TTS audio playing).');
			return;
		}
		try {
			await chatStore.sendMessage(text, undefined, isHidden);
		} catch (error) {
			console.error('Failed to send message from avatar interact:', error);
		}
	};
}
