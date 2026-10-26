import Dexie, { type EntityTable } from 'dexie';
import { findDescendantMessages, uuid, filterByLeafNodeId } from '$lib/utils';
import { IDXDB_TABLES, IDXDB_STORES, STORAGE_APP_NAME } from '$lib/constants';
import { getGatewayBaseUrl } from '$lib/constants/api-endpoints';
import { MessageRole } from '$lib/enums';
import type { McpServerOverride } from '$lib/types/database';

export interface ServerSessionSummary {
	id: string;
	name: string;
	updatedAt?: string;
	lastModified?: number;
	currNode?: string;
}

export interface SessionBundle extends DatabaseConversation {
	messages: DatabaseMessage[];
}

class LlamaUiDatabase extends Dexie {
	[IDXDB_TABLES.conversations]!: EntityTable<DatabaseConversation, string>;
	[IDXDB_TABLES.messages]!: EntityTable<DatabaseMessage, string>;

	constructor() {
		super(STORAGE_APP_NAME);

		this.version(1).stores(IDXDB_STORES);
	}
}

const db = new LlamaUiDatabase();

export class DatabaseService {
	/**
	 *
	 *
	 * Conversations
	 *
	 *
	 */

	/**
	 * Creates a new conversation.
	 *
	 * @param name - Name of the conversation
	 * @returns The created conversation
	 */
	static async createConversation(name: string): Promise<DatabaseConversation> {
		const conversation: DatabaseConversation = {
			id: uuid(),
			name,
			lastModified: Date.now(),
			currNode: '',
			lastSummarizedIndex: 0,
			aiHistoryText: '',
			aiHistoryTextList: [],
			isSlidingActive: false
		};

		await db[IDXDB_TABLES.conversations].add(conversation);
		this.syncPushSession(conversation.id).catch(() => {});
		return conversation;
	}

	/**
	 *
	 *
	 * Messages
	 *
	 *
	 */

	/**
	 * Creates a new message branch by adding a message and updating parent/child relationships.
	 * Also updates the conversation's currNode to point to the new message.
	 *
	 * @param message - Message to add (without id)
	 * @param parentId - Parent message ID to attach to
	 * @returns The created message
	 */
	static async createMessageBranch(
		message: Omit<DatabaseMessage, 'id'>,
		parentId: string | null
	): Promise<DatabaseMessage> {
		const result = await db.transaction(
			'rw',
			[db[IDXDB_TABLES.conversations], db[IDXDB_TABLES.messages]],
			async () => {
				let parentMessage: DatabaseMessage | undefined;
				let msgMap: Map<string, DatabaseMessage> | undefined;
				if (parentId !== null) {
					const convMessages = await db[IDXDB_TABLES.messages]
						.where('convId')
						.equals(message.convId)
						.toArray();
					msgMap = new Map<string, DatabaseMessage>(convMessages.map((m) => [m.id, m]));
					parentMessage = msgMap.get(parentId) ?? (await db[IDXDB_TABLES.messages].get(parentId));
					if (!parentMessage) {
						throw new Error(`Parent message ${parentId} not found`);
					}
				}

				let snapshotHistoryText = '';
				let snapshotLastSummarizedIndex = 0;
				let snapshotHistoryList: string[] = [];
				let snapshotIsSliding = false;

				// 親ノードおよびその祖先（親方向）から最も近いスナップショットを探索（世界線分岐時の要約ロールバック）
				// 同時に親ノードまでの対話メッセージ件数を集計し、親のスナップショットインデックスが対話長を超えないようサニタイズ（コンテキスト消失防止）
				// ※ 会話テーブルの現在値（別世界線のデータ）は混入させない
				let currentParent: DatabaseMessage | undefined = parentMessage;
				let foundSnapshot = false;
				let ancestorDialogueCount = 0;

				while (currentParent) {
					if (
						currentParent.type !== 'root' &&
						currentParent.role !== MessageRole.SYSTEM &&
						currentParent.role !== 'system'
					) {
						ancestorDialogueCount++;
					}

					if (!foundSnapshot && currentParent.aiHistoryTextListSnapshot !== undefined) {
						snapshotHistoryText = currentParent.aiHistoryTextSnapshot ?? '';
						snapshotLastSummarizedIndex = currentParent.lastSummarizedIndexSnapshot ?? 0;
						snapshotHistoryList = [...(currentParent.aiHistoryTextListSnapshot ?? [])];
						snapshotIsSliding = currentParent.isSlidingActiveSnapshot ?? false;
						foundSnapshot = true;
					}

					if (currentParent.parent) {
						currentParent =
							msgMap?.get(currentParent.parent) ??
							(await db[IDXDB_TABLES.messages].get(currentParent.parent));
					} else {
						break;
					}
				}

				// 親ノードまでの対話件数を超えている不正なスナップショットインデックスをサニタイズ（新規メッセージが未要約なのに要約済みと誤認されるのを防ぐ）
				if (snapshotLastSummarizedIndex > ancestorDialogueCount) {
					console.warn(
						`[createMessageBranch] Clamping snapshotLastSummarizedIndex (${snapshotLastSummarizedIndex}) to ancestorDialogueCount (${ancestorDialogueCount})`
					);
					snapshotLastSummarizedIndex = ancestorDialogueCount;
					if (ancestorDialogueCount === 0) {
						snapshotHistoryList = [];
						snapshotHistoryText = '';
						snapshotIsSliding = false;
					}
				}

				const newMessage: DatabaseMessage = {
					...message,
					id: uuid(),
					parent: parentId,
					toolCalls: message.toolCalls ?? '',
					children: [],
					aiHistoryTextSnapshot: snapshotHistoryText,
					lastSummarizedIndexSnapshot: snapshotLastSummarizedIndex,
					aiHistoryTextListSnapshot: snapshotHistoryList,
					isSlidingActiveSnapshot: snapshotIsSliding
				};

				await db[IDXDB_TABLES.messages].add(newMessage);

				// Update parent's children array if parent exists
				if (parentId !== null && parentMessage) {
					await db[IDXDB_TABLES.messages].update(parentId, {
						children: [...parentMessage.children, newMessage.id]
					});
				}

				await this.updateConversation(message.convId, {
					currNode: newMessage.id,
					aiHistoryText: snapshotHistoryText,
					lastSummarizedIndex: snapshotLastSummarizedIndex,
					aiHistoryTextList: snapshotHistoryList,
					isSlidingActive: snapshotIsSliding
				});

				return newMessage;
			}
		);
		this.syncPushSessionDebounced(message.convId, 500);
		return result;
	}

	/**
	 * Creates a root message for a new conversation.
	 * Root messages are not displayed but serve as the tree root for branching.
	 *
	 * @param convId - Conversation ID
	 * @returns The created root message
	 */
	static async createRootMessage(convId: string): Promise<string> {
		const rootMessage: DatabaseMessage = {
			id: uuid(),
			convId,
			type: 'root',
			timestamp: Date.now(),
			role: MessageRole.SYSTEM,
			content: '',
			parent: null,
			toolCalls: '',
			children: []
		};

		await db[IDXDB_TABLES.messages].add(rootMessage);
		return rootMessage.id;
	}

	/**
	 * Creates a system prompt message for a conversation.
	 *
	 * @param convId - Conversation ID
	 * @param systemPrompt - The system prompt content (must be non-empty)
	 * @param parentId - Parent message ID (typically the root message)
	 * @returns The created system message
	 * @throws Error if systemPrompt is empty
	 */
	static async createSystemMessage(
		convId: string,
		systemPrompt: string,
		parentId: string
	): Promise<DatabaseMessage> {
		const trimmedPrompt = systemPrompt.trim();
		if (!trimmedPrompt) {
			throw new Error('Cannot create system message with empty content');
		}

		const systemMessage: DatabaseMessage = {
			id: uuid(),
			convId,
			type: MessageRole.SYSTEM,
			timestamp: Date.now(),
			role: MessageRole.SYSTEM,
			content: trimmedPrompt,
			parent: parentId,
			children: []
		};

		await db[IDXDB_TABLES.messages].add(systemMessage);

		const parentMessage = await db[IDXDB_TABLES.messages].get(parentId);
		if (parentMessage) {
			await db[IDXDB_TABLES.messages].update(parentId, {
				children: [...parentMessage.children, systemMessage.id]
			});
		}

		return systemMessage;
	}

	/**
	 * Deletes a conversation and all its messages.
	 *
	 * @param id - Conversation ID
	 */
	static async deleteConversation(
		id: string,
		options?: { deleteWithForks?: boolean }
	): Promise<void> {
		const deletedIds: string[] = [id];
		await db.transaction(
			'rw',
			[db[IDXDB_TABLES.conversations], db[IDXDB_TABLES.messages]],
			async () => {
				if (options?.deleteWithForks) {
					// Recursively collect all descendant IDs
					const idsToDelete: string[] = [];
					const queue = [id];

					while (queue.length > 0) {
						const parentId = queue.pop()!;
						const children = await db[IDXDB_TABLES.conversations]
							.filter((c) => c.forkedFromConversationId === parentId)
							.toArray();

						for (const child of children) {
							idsToDelete.push(child.id);
							queue.push(child.id);
						}
					}

					for (const forkId of idsToDelete) {
						deletedIds.push(forkId);
						await db[IDXDB_TABLES.conversations].delete(forkId);
						await db[IDXDB_TABLES.messages].where('convId').equals(forkId).delete();
					}
				} else {
					// Reparent direct children to deleted conv's parent
					const conv = await db[IDXDB_TABLES.conversations].get(id);
					const newParent = conv?.forkedFromConversationId;
					const directChildren = await db[IDXDB_TABLES.conversations]
						.filter((c) => c.forkedFromConversationId === id)
						.toArray();

					for (const child of directChildren) {
						await db[IDXDB_TABLES.conversations].update(child.id, {
							forkedFromConversationId: newParent ?? undefined
						});
					}
				}

				await db[IDXDB_TABLES.conversations].delete(id);
				await db[IDXDB_TABLES.messages].where('convId').equals(id).delete();
			}
		);
		for (const delId of deletedIds) {
			this.syncDeleteSession(delId).catch(() => {});
		}
	}

	/**
	 * Deletes a message and removes it from its parent's children array.
	 *
	 * @param messageId - ID of the message to delete
	 */
	static async deleteMessage(messageId: string): Promise<void> {
		let convId: string | undefined;
		await db.transaction('rw', db[IDXDB_TABLES.messages], async () => {
			const message = await db[IDXDB_TABLES.messages].get(messageId);
			if (!message) return;
			convId = message.convId;

			// Remove this message from its parent's children array
			if (message.parent) {
				const parent = await db[IDXDB_TABLES.messages].get(message.parent);
				if (parent) {
					parent.children = parent.children.filter((childId: string) => childId !== messageId);
					await db[IDXDB_TABLES.messages].put(parent);
				}
			}

			// Delete the message
			await db[IDXDB_TABLES.messages].delete(messageId);
		});
		if (convId) {
			this.syncPushSessionDebounced(convId, 500);
		}
	}

	/**
	 * Deletes a message and all its descendant messages (cascading deletion).
	 * This removes the entire branch starting from the specified message.
	 *
	 * @param conversationId - ID of the conversation containing the message
	 * @param messageId - ID of the root message to delete (along with all descendants)
	 * @returns Array of all deleted message IDs
	 */
	static async deleteMessageCascading(
		conversationId: string,
		messageId: string
	): Promise<string[]> {
		const allToDelete = await db.transaction('rw', db[IDXDB_TABLES.messages], async () => {
			// Get all messages in the conversation to find descendants
			const allMessages = await db[IDXDB_TABLES.messages]
				.where('convId')
				.equals(conversationId)
				.toArray();

			// Find all descendant messages
			const descendants = findDescendantMessages(allMessages, messageId);
			const allToDelete = [messageId, ...descendants];

			// Get the message to delete for parent cleanup
			const message = await db[IDXDB_TABLES.messages].get(messageId);
			if (message && message.parent) {
				const parent = await db[IDXDB_TABLES.messages].get(message.parent);
				if (parent) {
					parent.children = parent.children.filter((childId: string) => childId !== messageId);
					await db[IDXDB_TABLES.messages].put(parent);
				}
			}

			// Delete all messages in the branch
			await db[IDXDB_TABLES.messages].bulkDelete(allToDelete);

			return allToDelete;
		});
		this.syncPushSessionDebounced(conversationId, 500);
		return allToDelete;
	}

	/**
	 * Gets all conversations, sorted by last modified time (newest first).
	 *
	 * @returns Array of conversations
	 */
	static async getAllConversations(): Promise<DatabaseConversation[]> {
		return await db[IDXDB_TABLES.conversations].orderBy('lastModified').reverse().toArray();
	}

	/**
	 * Gets a conversation by ID.
	 *
	 * @param id - Conversation ID
	 * @returns The conversation if found, otherwise undefined
	 */
	static async getConversation(id: string): Promise<DatabaseConversation | undefined> {
		return await db[IDXDB_TABLES.conversations].get(id);
	}

	/**
	 * Gets all messages in a conversation, sorted by timestamp (oldest first).
	 *
	 * @param convId - Conversation ID
	 * @returns Array of messages in the conversation
	 */
	static async getConversationMessages(convId: string): Promise<DatabaseMessage[]> {
		return await db[IDXDB_TABLES.messages].where('convId').equals(convId).sortBy('timestamp');
	}

	/**
	 * Gets a message by its ID.
	 *
	 * @param id - Message ID
	 * @returns The message, or undefined if not found
	 */
	static async getMessage(id: string): Promise<DatabaseMessage | undefined> {
		return await db[IDXDB_TABLES.messages].get(id);
	}

	/**
	 * Updates a conversation.
	 *
	 * @param id - Conversation ID
	 * @param updates - Partial updates to apply
	 * @returns Promise that resolves when the conversation is updated
	 */
	static async updateConversation(
		id: string,
		updates: Partial<Omit<DatabaseConversation, 'id'>>
	): Promise<void> {
		await db[IDXDB_TABLES.conversations].update(id, {
			...updates,
			lastModified: Date.now()
		});
		this.syncPushSessionDebounced(id, 500);
	}

	/**
	 *
	 *
	 * Navigation
	 *
	 *
	 */

	/**
	 * Toggles the pinned status of a conversation.
	 *
	 * @param id - Conversation ID
	 * @returns The new pinned status
	 */
	static async toggleConversationPin(id: string): Promise<boolean> {
		const conversation = await db.conversations.get(id);
		if (!conversation) {
			throw new Error(`Conversation ${id} not found`);
		}
		const newPinnedState = !conversation.pinned;
		await this.updateConversation(id, { pinned: newPinnedState });
		return newPinnedState;
	}

	/**
	 * Updates the conversation's current node (active branch).
	 * This determines which conversation path is currently being viewed.
	 *
	 * @param convId - Conversation ID
	 * @param nodeId - Message ID to set as current node
	 */
	static async updateCurrentNode(convId: string, nodeId: string): Promise<void> {
		await this.updateConversation(convId, {
			currNode: nodeId
		});
	}

	/**
	 * Updates a message.
	 *
	 * @param id - Message ID
	 * @param updates - Partial updates to apply
	 * @returns Promise that resolves when the message is updated
	 */
	static async updateMessage(
		id: string,
		updates: Partial<Omit<DatabaseMessage, 'id'>>
	): Promise<void> {
		await db[IDXDB_TABLES.messages].update(id, updates);
		const msg = await db[IDXDB_TABLES.messages].get(id);
		if (msg && msg.convId) {
			this.syncPushSessionDebounced(msg.convId, 1000);
		}
	}

	static async putMessage(message: DatabaseMessage): Promise<string> {
		const res = await db[IDXDB_TABLES.messages].put(message);
		if (message.convId) {
			this.syncPushSessionDebounced(message.convId, 1000);
		}
		return res;
	}

	/**
	 *
	 *
	 * Import
	 *
	 *
	 */

	/**
	 * Imports multiple conversations and their messages.
	 * Skips conversations that already exist.
	 *
	 * @param data - Array of { conv, messages } objects
	 */
	static async importConversations(
		data: { conv: DatabaseConversation; messages: DatabaseMessage[] }[]
	): Promise<{ imported: number; skipped: number; importedConversations: DatabaseConversation[] }> {
		let importedCount = 0;
		let skippedCount = 0;
		const importedConversations: DatabaseConversation[] = [];

		return await db.transaction(
			'rw',
			[db[IDXDB_TABLES.conversations], db[IDXDB_TABLES.messages]],
			async () => {
				for (const item of data) {
					let { conv, messages } = item;

					const existing = await db[IDXDB_TABLES.conversations].get(conv.id);
					if (existing) {
						console.log(`Conversation "${conv.name}" already exists. Re-mapping IDs to create a copy...`);
						
						const newConvId = uuid();
						const idMap = new Map<string, string>();
						
						for (const msg of messages) {
							idMap.set(msg.id, uuid());
						}
						
						const copiedMessages: DatabaseMessage[] = messages.map((msg) => {
							const newMsgId = idMap.get(msg.id)!;
							const newParentId = msg.parent ? (idMap.get(msg.parent) ?? msg.parent) : null;
							const newChildren = msg.children ? msg.children.map(cId => idMap.get(cId) ?? cId) : [];
							
							return {
								...msg,
								id: newMsgId,
								convId: newConvId,
								parent: newParentId,
								children: newChildren
							};
						});
						
						const newCurrNode = conv.currNode ? (idMap.get(conv.currNode) ?? conv.currNode) : '';
						conv = {
							...conv,
							id: newConvId,
							name: `${conv.name} (Copy)`,
							currNode: newCurrNode
						};
						messages = copiedMessages;
					}

					await db[IDXDB_TABLES.conversations].add(conv);
					for (const msg of messages) {
						await db[IDXDB_TABLES.messages].put(msg);
					}

					importedCount++;
					importedConversations.push(conv);
				}

				return { imported: importedCount, skipped: skippedCount, importedConversations };
			}
		);
	}

	/**
	 *
	 *
	 * Forking
	 *
	 *
	 */

	/**
	 * Forks a conversation at a specific message, creating a new conversation
	 * containing all messages from the root up to (and including) the target message.
	 *
	 * @param sourceConvId - The source conversation ID
	 * @param atMessageId - The message ID to fork at (the new conversation ends here)
	 * @param options - Fork options (name and whether to include attachments)
	 * @returns The newly created conversation
	 */
	static async forkConversation(
		sourceConvId: string,
		atMessageId: string,
		options: { name: string; includeAttachments: boolean }
	): Promise<DatabaseConversation> {
		return await db.transaction(
			'rw',
			[db[IDXDB_TABLES.conversations], db[IDXDB_TABLES.messages]],
			async () => {
				const sourceConv = await db[IDXDB_TABLES.conversations].get(sourceConvId);
				if (!sourceConv) {
					throw new Error(`Source conversation ${sourceConvId} not found`);
				}

				const allMessages = await db[IDXDB_TABLES.messages]
					.where('convId')
					.equals(sourceConvId)
					.toArray();

				const pathMessages = filterByLeafNodeId(
					allMessages,
					atMessageId,
					true
				) as DatabaseMessage[];
				if (pathMessages.length === 0) {
					throw new Error(`Could not resolve message path to ${atMessageId}`);
				}

				const idMap = new Map<string, string>();

				for (const msg of pathMessages) {
					idMap.set(msg.id, uuid());
				}

				const newConvId = uuid();
				const clonedMessages: DatabaseMessage[] = pathMessages.map((msg) => {
					const newId = idMap.get(msg.id)!;
					const newParent = msg.parent ? (idMap.get(msg.parent) ?? null) : null;
					const newChildren = msg.children
						.filter((childId: string) => idMap.has(childId))
						.map((childId: string) => idMap.get(childId)!);

					return {
						...msg,
						id: newId,
						convId: newConvId,
						parent: newParent,
						children: newChildren,
						extra: options.includeAttachments ? msg.extra : undefined
					};
				});

				const lastClonedMessage = clonedMessages[clonedMessages.length - 1];
				const newConv: DatabaseConversation = {
					id: newConvId,
					name: options.name,
					lastModified: Date.now(),
					currNode: lastClonedMessage.id,
					forkedFromConversationId: sourceConvId,
					mcpServerOverrides: sourceConv.mcpServerOverrides
						? sourceConv.mcpServerOverrides.map((o: McpServerOverride) => ({
								serverId: o.serverId,
								enabled: o.enabled
							}))
						: undefined
				};

				await db[IDXDB_TABLES.conversations].add(newConv);

				for (const msg of clonedMessages) {
					await db[IDXDB_TABLES.messages].add(msg);
				}

				return newConv;
			}
		);
		this.syncPushSession(newConv.id).catch(() => {});
		return newConv;
	}
	/**
	 *
	 *
	 * Server Synchronization (Server-Master & Local Cache)
	 *
	 *
	 */

	private static pushDebounceTimers = new Map<string, ReturnType<typeof setTimeout>>();

	/**
	 * Exports a complete session bundle (conversation metadata + all messages)
	 */
	static async exportSessionBundle(convId: string): Promise<SessionBundle | null> {
		const conv = await db[IDXDB_TABLES.conversations].get(convId);
		if (!conv) return null;
		const messages = await db[IDXDB_TABLES.messages]
			.where('convId')
			.equals(convId)
			.sortBy('timestamp');
		return {
			...conv,
			messages
		};
	}

	/**
	 * Imports or updates a session bundle into local IndexedDB.
	 * Overwrites if already exists.
	 */
	static async importOrUpdateSessionBundle(bundle: any): Promise<boolean> {
		if (!bundle || !bundle.id) return false;

		const { messages: rawMessages, ...convData } = bundle;
		const messages: DatabaseMessage[] = Array.isArray(rawMessages)
			? rawMessages
			: typeof rawMessages === 'object' && rawMessages !== null
				? Object.values(rawMessages)
				: [];

		const conv: DatabaseConversation = {
			id: convData.id,
			name: convData.name || 'Chat',
			lastModified: Number(convData.lastModified) || Date.now(),
			currNode: convData.currNode || '',
			lastSummarizedIndex: convData.lastSummarizedIndex ?? 0,
			aiHistoryText: convData.aiHistoryText ?? '',
			aiHistoryTextList: convData.aiHistoryTextList ?? [],
			isSlidingActive: convData.isSlidingActive ?? false,
			pinned: convData.pinned ?? false,
			forkedFromConversationId: convData.forkedFromConversationId ?? undefined,
			mcpServerOverrides: convData.mcpServerOverrides ?? undefined,
			thinkingEnabled: convData.thinkingEnabled ?? undefined,
			reasoningEffort: convData.reasoningEffort ?? undefined,
			archivedSummaries: convData.archivedSummaries ?? undefined
		};

		await db.transaction(
			'rw',
			[db[IDXDB_TABLES.conversations], db[IDXDB_TABLES.messages]],
			async () => {
				await db[IDXDB_TABLES.conversations].put(conv);
				if (messages.length > 0) {
					await db[IDXDB_TABLES.messages].bulkPut(messages);
				}
			}
		);
		return true;
	}

	/**
	 * Pushes a local conversation and all its messages to the backend server.
	 */
	static async syncPushSession(convId: string): Promise<boolean> {
		try {
			const bundle = await this.exportSessionBundle(convId);
			if (!bundle) return false;

			const url = `${getGatewayBaseUrl()}/api/sessions/${convId}`;
			const res = await fetch(url, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(bundle)
			});
			return res.ok;
		} catch (error) {
			console.warn(`[Sync] Failed to push session ${convId}:`, error);
			return false;
		}
	}

	/**
	 * Pushes a local conversation to the backend server with debouncing.
	 */
	static syncPushSessionDebounced(convId: string, delayMs = 500): void {
		if (this.pushDebounceTimers.has(convId)) {
			clearTimeout(this.pushDebounceTimers.get(convId)!);
		}
		const timer = setTimeout(() => {
			this.pushDebounceTimers.delete(convId);
			this.syncPushSession(convId).catch((err) => {
				console.warn(`[Sync] Debounced push failed for ${convId}:`, err);
			});
		}, delayMs);
		this.pushDebounceTimers.set(convId, timer);
	}

	/**
	 * Pulls a conversation and all its messages from the backend server to local IndexedDB.
	 */
	static async syncPullSession(convId: string): Promise<boolean> {
		try {
			const url = `${getGatewayBaseUrl()}/api/sessions/${convId}`;
			const res = await fetch(url);
			if (!res.ok) return false;

			const bundle = await res.json();
			return await this.importOrUpdateSessionBundle(bundle);
		} catch (error) {
			console.warn(`[Sync] Failed to pull session ${convId}:`, error);
			return false;
		}
	}

	/**
	 * Deletes a conversation from the backend server.
	 */
	static async syncDeleteSession(convId: string): Promise<boolean> {
		try {
			const url = `${getGatewayBaseUrl()}/api/sessions/${convId}`;
			const res = await fetch(url, { method: 'DELETE' });
			return res.ok;
		} catch (error) {
			console.warn(`[Sync] Failed to delete session ${convId} on server:`, error);
			return false;
		}
	}

	/**
	 * Synchronizes all sessions between local IndexedDB and the backend server.
	 * Bidirectional merge:
	 * - Pushes local sessions missing on server or newer locally.
	 * - Pulls server sessions missing locally or newer on server.
	 */
	static async syncAllSessions(): Promise<{ pulled: number; pushed: number }> {
		let pulled = 0;
		let pushed = 0;

		try {
			const listUrl = `${getGatewayBaseUrl()}/api/sessions`;
			const res = await fetch(listUrl);
			if (!res.ok) return { pulled, pushed };

			const serverSessions: ServerSessionSummary[] = await res.json();
			const localSessions = await this.getAllConversations();

			const serverMap = new Map<string, ServerSessionSummary>();
			for (const s of serverSessions) {
				serverMap.set(s.id, s);
			}

			const localMap = new Map<string, DatabaseConversation>();
			for (const l of localSessions) {
				localMap.set(l.id, l);
			}

			// 1. Local exists, server missing or local newer -> Push
			for (const local of localSessions) {
				const remote = serverMap.get(local.id);
				const localMod = Number(local.lastModified) || 0;
				const remoteMod = remote ? Number(remote.lastModified) || 0 : 0;

				if (!remote || localMod > remoteMod) {
					const ok = await this.syncPushSession(local.id);
					if (ok) pushed++;
				}
			}

			// 2. Server exists, local missing or server newer -> Pull
			for (const remote of serverSessions) {
				const local = localMap.get(remote.id);
				const remoteMod = Number(remote.lastModified) || 0;
				const localMod = local ? Number(local.lastModified) || 0 : 0;

				if (!local || remoteMod > localMod) {
					const ok = await this.syncPullSession(remote.id);
					if (ok) pulled++;
				}
			}
		} catch (error) {
			console.warn('[Sync] Session synchronization encountered an error:', error);
		}

		return { pulled, pushed };
	}
}
