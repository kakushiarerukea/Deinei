<script lang="ts">
	import {
		ChatAttachmentsList,
		ChatFormActions,
		ChatFormFileInputInvisible,
		ChatFormMcpResourcesList,
		ChatFormPickers,
		ChatFormTextarea,
		DialogMcpResourcesBrowser
	} from '$lib/components/app';
	import ChatScreenContextGauge from '../ChatScreen/ChatScreenContextGauge.svelte';
	import { t } from '$lib/stores/i18n.svelte';
	import {
		CLIPBOARD_CONTENT_QUOTE_PREFIX,
		INPUT_CLASSES,
		SETTING_CONFIG_DEFAULT,
		INITIAL_FILE_SIZE,
		PROMPT_CONTENT_SEPARATOR,
		PROMPT_TRIGGER_PREFIX,
		RESOURCE_TRIGGER_PREFIX
	} from '$lib/constants';
	import { getGatewayBaseUrl } from '$lib/constants/api-endpoints';
	import {
		ContentPartType,
		FileExtensionText,
		KeyboardKey,
		MimeTypeText,
		SpecialFileType
	} from '$lib/enums';
	import { config } from '$lib/stores/settings.svelte';
	import { modelOptions, selectedModelId } from '$lib/stores/models.svelte';
	import { isRouterMode } from '$lib/stores/server.svelte';
	import { chatStore, activeProcessingState } from '$lib/stores/chat.svelte';
	import { mcpStore } from '$lib/stores/mcp.svelte';
	import { mcpHasResourceAttachments } from '$lib/stores/mcp-resources.svelte';
	import { conversationsStore, activeMessages } from '$lib/stores/conversations.svelte';
	import type { GetPromptResult, MCPPromptInfo, MCPResourceInfo, PromptMessage } from '$lib/types';
	import { isIMEComposing, parseClipboardContent, uuid } from '$lib/utils';
	import {
		AudioRecorder,
		convertToWav,
		createAudioFile,
		isAudioRecordingSupported
	} from '$lib/utils/browser-only';
	import { onMount } from 'svelte';

	interface Props {
		// Data
		attachments?: DatabaseMessageExtra[];
		uploadedFiles?: ChatUploadedFile[];
		value?: string;

		// UI State
		class?: string;
		disabled?: boolean;
		isLoading?: boolean;
		placeholder?: string;
		showMcpPromptButton?: boolean;
		showAddButton?: boolean;
		showModelSelector?: boolean;

		// Event Handlers
		onAttachmentRemove?: (index: number) => void;
		onFilesAdd?: (files: File[]) => void;
		onStop?: () => void;
		onSubmit?: () => void;
		onSystemPromptClick?: (draft: { message: string; files: ChatUploadedFile[] }) => void;
		onUploadedFileRemove?: (fileId: string) => void;
		onUploadedFilesChange?: (files: ChatUploadedFile[]) => void;
		onValueChange?: (value: string) => void;
	}

	let {
		attachments = [],
		class: className = '',
		disabled = false,
		isLoading = false,
		placeholder = 'Type a message...',
		showMcpPromptButton = false,
		showAddButton = true,
		showModelSelector = true,
		uploadedFiles = $bindable([]),
		value = $bindable(''),
		onAttachmentRemove,
		onFilesAdd,
		onStop,
		onSubmit,
		onSystemPromptClick,
		onUploadedFileRemove,
		onUploadedFilesChange,
		onValueChange
	}: Props = $props();

	// Component References
	let audioRecorder: AudioRecorder | undefined;
	let chatFormActionsRef: ChatFormActions | undefined = $state(undefined);
	let fileInputRef: ChatFormFileInputInvisible | undefined = $state(undefined);
	let pickersRef: { handleKeydown: (event: KeyboardEvent) => boolean } | undefined =
		$state(undefined);
	let textareaRef: ChatFormTextarea | undefined = $state(undefined);

	const currentProcessing = $derived(activeProcessingState());

	// Audio Recording State
	let isRecording = $state(false);
	let recordingSupported = $state(false);
	let isTranscribing = $state(false);
	let isFormDisabled = $derived(disabled || isTranscribing);
	let computedPlaceholder = $derived(isTranscribing ? t('chat.transcribing', 'Transcribing audio...') : placeholder);

	// Picker State
	let isPromptPickerOpen = $state(false);
	let promptSearchQuery = $state('');
	let isInlineResourcePickerOpen = $state(false);
	let resourceSearchQuery = $state('');

	// Resource Dialog State
	let isResourceDialogOpen = $state(false);
	let preSelectedResourceUri = $state<string | undefined>(undefined);

	let currentConfig = $derived(config());

	let pasteLongTextToFileLength = $derived.by(() => {
		const n = Number(currentConfig.pasteLongTextToFileLen);
		return Number.isNaN(n) ? Number(SETTING_CONFIG_DEFAULT.pasteLongTextToFileLen) : n;
	});

	let isRouter = $derived(isRouterMode());
	let conversationModel = $derived(
		chatStore.getConversationModel(activeMessages() as DatabaseMessage[])
	);
	let activeModelId = $derived.by(() => {
		const options = modelOptions();

		if (!isRouter) {
			return options.length > 0 ? options[0].model : null;
		}

		const selectedId = selectedModelId();
		if (selectedId) {
			const model = options.find((m) => m.id === selectedId);
			if (model) return model.model;
		}

		if (conversationModel) {
			const model = options.find((m) => m.model === conversationModel);
			if (model) return model.model;
		}

		return null;
	});

	let hasModelSelected = $derived(!isRouter || !!conversationModel || !!selectedModelId());
	let hasLoadingAttachments = $derived(uploadedFiles.some((f) => f.isLoading));
	let hasAttachments = $derived(
		(attachments && attachments.length > 0) || (uploadedFiles && uploadedFiles.length > 0)
	);
	let canSubmit = $derived(true);

	let audioContextForSilence: AudioContext | undefined;
	let analyserForSilence: AnalyserNode | undefined;
	let silenceDetectionFrameId: number | null = null;

	const BASE_MIN_THRESHOLD = 0.015;       // 固定の下限閾値 (RMS)
	const SILENCE_DURATION = 1500;          // 無音が続いてから停止するまでの時間 (ms)
	const NOISE_CALIBRATION_MS = 500;       // 録音開始後の環境ノイズ測定期間 (ms)
	const MIN_SPEECH_DURATION_MS = 300;     // 有効発話判定に必要な最小累計発話時間 (ms)
	const MAX_RECORDING_DURATION_MS = 30000; // 最大録音時間 (30秒)

	function startSilenceDetection(stream: MediaStream) {
		if (audioContextForSilence) {
			audioContextForSilence.close();
		}
		
		const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
		audioContextForSilence = new AudioContextClass();
		analyserForSilence = audioContextForSilence.createAnalyser();
		const source = audioContextForSilence.createMediaStreamSource(stream);
		source.connect(analyserForSilence);
		analyserForSilence.fftSize = 512;
		
		const bufferLength = analyserForSilence.fftSize;
		const dataArray = new Float32Array(bufferLength);
		
		const recordingStartTime = Date.now();
		let lastFrameTime = recordingStartTime;
		let lastLogTime = 0;

		const noiseSamples: number[] = [];
		let noiseEstimated = false;
		let estimatedNoiseRms = 0;
		let adaptiveThreshold = BASE_MIN_THRESHOLD;

		let hasSpoken = false;
		let totalSpeechMs = 0;
		let silenceStartTime = 0;

		function checkAudio() {
			if (!isRecording) return;
			
			if (analyserForSilence) {
				const now = Date.now();
				const dt = Math.max(0, now - lastFrameTime);
				lastFrameTime = now;

				analyserForSilence.getFloatTimeDomainData(dataArray);
				
				let sum = 0;
				for (let i = 0; i < bufferLength; i++) {
					sum += dataArray[i] * dataArray[i];
				}
				const rms = Math.sqrt(sum / bufferLength);

				// 1. 環境ノイズの適応測定 (開始後約0.5秒)
				if (now - recordingStartTime < NOISE_CALIBRATION_MS) {
					noiseSamples.push(rms);
				} else if (!noiseEstimated) {
					noiseEstimated = true;
					const avgNoise = noiseSamples.length > 0
						? noiseSamples.reduce((a, b) => a + b, 0) / noiseSamples.length
						: 0;
					estimatedNoiseRms = avgNoise;
					// 閾値 = max(固定の下限, ノイズ × 1.6)
					adaptiveThreshold = Math.max(BASE_MIN_THRESHOLD, avgNoise * 1.6);
					console.log(`🎙️ [STT VAD] ノイズ適応完了: 推定ノイズ=${avgNoise.toFixed(4)}, 適応閾値=${adaptiveThreshold.toFixed(4)}`);
				}

				const currentThreshold = adaptiveThreshold;

				// 2. 発話判定と無音タイマー
				if (rms > currentThreshold) {
					hasSpoken = true;
					totalSpeechMs += dt;
					silenceStartTime = 0; // リセット
				} else if (hasSpoken) {
					if (silenceStartTime === 0) {
						silenceStartTime = now;
					} else if (now - silenceStartTime > SILENCE_DURATION) {
						console.log(`🔊 [STT VAD] 無音検知: 録音停止 (発話累計: ${totalSpeechMs}ms)`);
						handleMicClick(true, totalSpeechMs >= MIN_SPEECH_DURATION_MS);
						return;
					}
				}

				// 3. 最大録音時間 (30秒) ガード
				if (now - recordingStartTime >= MAX_RECORDING_DURATION_MS) {
					console.log(`⏱️ [STT VAD] 最大録音時間(30秒)到達: 強制停止 (発話累計: ${totalSpeechMs}ms)`);
					handleMicClick(true, totalSpeechMs >= MIN_SPEECH_DURATION_MS);
					return;
				}

				// 4. デバッグログ (1秒ごと)
				if (now - lastLogTime >= 1000) {
					lastLogTime = now;
					const elapsedSec = ((now - recordingStartTime) / 1000).toFixed(1);
					console.log(
						`🎙️ [STT VAD ${elapsedSec}s] 音量: ${rms.toFixed(4)} | 閾値: ${currentThreshold.toFixed(4)} | ノイズ推定: ${estimatedNoiseRms.toFixed(4)} | 発話累計: ${totalSpeechMs}ms`
					);
				}
			}
			
			silenceDetectionFrameId = requestAnimationFrame(checkAudio);
		}
		
		silenceDetectionFrameId = requestAnimationFrame(checkAudio);
	}

	function cleanupSilenceDetection() {
		if (silenceDetectionFrameId) {
			cancelAnimationFrame(silenceDetectionFrameId);
			silenceDetectionFrameId = null;
		}
		if (audioContextForSilence) {
			audioContextForSilence.close();
			audioContextForSilence = undefined;
		}
		analyserForSilence = undefined;
	}

	onMount(() => {
		recordingSupported = isAudioRecordingSupported();
		audioRecorder = new AudioRecorder();

		const handleTtsState = (e: any) => {
			const isSpeaking = e.detail.isPlaying;
			const isVoiceChat = localStorage.getItem('deining_voice_chat_enabled') === 'true';

			// TTS再生開始を検知した場合はスピーカー音の回り込み防止のため録音を一時中断
			if (isSpeaking && isRecording) {
				console.log("🔊 TTS再生開始を検知：スピーカー音の回り込み防止のため録音を一時中断します。");
				cleanupSilenceDetection();
				isRecording = false;
				try {
					audioRecorder?.cancelRecording();
				} catch (err) {
					console.error(err);
				}
				return;
			}

			if (!isSpeaking && isVoiceChat && !isRecording && !isTranscribing && !isLoading) {
				console.log("🔊 TTS発話完了：録音を自動開始します。");
				handleMicClick();
			}
		};

		const handleSettingsChanged = () => {
			const isVoiceChat = localStorage.getItem('deining_voice_chat_enabled') === 'true';
			if (isVoiceChat && !isRecording && !isTranscribing && !isLoading) {
				console.log("🔊 音声対話モードON：録音を自動開始します。");
				handleMicClick();
			} else if (!isVoiceChat && isRecording) {
				console.log("🔊 音声対話モードOFF：録音を停止します。");
				handleMicClick(false);
			}
		};

		window.addEventListener('deiningTtsStateChanged', handleTtsState as EventListener);
		window.addEventListener('deiningVoiceChatSettingsChanged', handleSettingsChanged);

		// 初期状態で音声対話がONの場合の自動開始処理
		setTimeout(() => {
			const isVoiceChat = localStorage.getItem('deining_voice_chat_enabled') === 'true';
			if (isVoiceChat && !isRecording && !isTranscribing && !isLoading) {
				handleMicClick();
			}
		}, 1000);

		return () => {
			window.removeEventListener('deiningTtsStateChanged', handleTtsState as EventListener);
			window.removeEventListener('deiningVoiceChatSettingsChanged', handleSettingsChanged);
			cleanupSilenceDetection();
		};
	});

	export function focus() {
		textareaRef?.focus();
	}

	export function resetTextareaHeight() {
		textareaRef?.resetHeight();
	}

	export function openModelSelector() {
		chatFormActionsRef?.openModelSelector();
	}

	export function checkModelSelected(): boolean {
		if (!hasModelSelected) {
			chatFormActionsRef?.openModelSelector();
			return false;
		}
		return true;
	}

	function handleFileSelect(files: File[]) {
		onFilesAdd?.(files);
	}

	function handleFileUpload() {
		fileInputRef?.click();
	}

	function handleFileRemove(fileId: string) {
		if (fileId.startsWith('attachment-')) {
			const index = parseInt(fileId.replace('attachment-', ''), 10);
			if (!isNaN(index) && index >= 0 && index < attachments.length) {
				onAttachmentRemove?.(index);
			}
		} else {
			onUploadedFileRemove?.(fileId);
		}
	}

	function handleInput() {
		const perChatOverrides = conversationsStore.getAllMcpServerOverrides();
		const hasServers = mcpStore.hasEnabledServers(perChatOverrides);

		if (value.startsWith(PROMPT_TRIGGER_PREFIX) && hasServers) {
			isPromptPickerOpen = true;
			promptSearchQuery = value.slice(1);
			isInlineResourcePickerOpen = false;
			resourceSearchQuery = '';
		} else if (
			value.startsWith(RESOURCE_TRIGGER_PREFIX) &&
			hasServers &&
			mcpStore.hasResourcesCapability(perChatOverrides)
		) {
			isInlineResourcePickerOpen = true;
			resourceSearchQuery = value.slice(1);
			isPromptPickerOpen = false;
			promptSearchQuery = '';
		} else {
			isPromptPickerOpen = false;
			promptSearchQuery = '';
			isInlineResourcePickerOpen = false;
			resourceSearchQuery = '';
		}
	}

	function handleKeydown(event: KeyboardEvent) {
		if (pickersRef?.handleKeydown(event)) {
			return;
		}

		if (event.key === KeyboardKey.ESCAPE && isPromptPickerOpen) {
			isPromptPickerOpen = false;
			promptSearchQuery = '';
			return;
		}

		if (event.key === KeyboardKey.ESCAPE && isInlineResourcePickerOpen) {
			isInlineResourcePickerOpen = false;
			resourceSearchQuery = '';
			return;
		}

		if (event.key === KeyboardKey.ENTER && !event.shiftKey && !isIMEComposing(event)) {
			const isModifier = event.ctrlKey || event.metaKey;
			const sendOnEnter = currentConfig.sendOnEnter !== false;

			if (sendOnEnter || isModifier) {
				event.preventDefault();

				if (!canSubmit || disabled || hasLoadingAttachments) return;

				onSubmit?.();
			}
		}
	}

	function handlePaste(event: ClipboardEvent) {
		if (!event.clipboardData) return;

		const files = Array.from(event.clipboardData.items)
			.filter((item) => item.kind === 'file')
			.map((item) => item.getAsFile())
			.filter((file): file is File => file !== null);

		if (files.length > 0) {
			event.preventDefault();
			onFilesAdd?.(files);
			return;
		}

		const text = event.clipboardData.getData(MimeTypeText.PLAIN);

		if (text.startsWith(CLIPBOARD_CONTENT_QUOTE_PREFIX)) {
			const parsed = parseClipboardContent(text);

			if (parsed.textAttachments.length > 0 || parsed.mcpPromptAttachments.length > 0) {
				event.preventDefault();
				value = parsed.message;
				onValueChange?.(parsed.message);

				// Handle text attachments as files
				if (parsed.textAttachments.length > 0) {
					const attachmentFiles = parsed.textAttachments.map(
						(att) =>
							new File([att.content], att.name, {
								type: MimeTypeText.PLAIN
							})
					);
					onFilesAdd?.(attachmentFiles);
				}

				// Handle MCP prompt attachments as ChatUploadedFile with mcpPrompt data
				if (parsed.mcpPromptAttachments.length > 0) {
					const mcpPromptFiles: ChatUploadedFile[] = parsed.mcpPromptAttachments.map((att) => ({
						id: uuid(),
						name: att.name,
						size: att.content.length,
						type: SpecialFileType.MCP_PROMPT,
						file: new File([att.content], `${att.name}${FileExtensionText.TXT}`, {
							type: MimeTypeText.PLAIN
						}),
						isLoading: false,
						textContent: att.content,
						mcpPrompt: {
							serverName: att.serverName,
							promptName: att.promptName,
							arguments: att.arguments
						}
					}));

					uploadedFiles = [...uploadedFiles, ...mcpPromptFiles];
					onUploadedFilesChange?.(uploadedFiles);
				}

				setTimeout(() => {
					textareaRef?.focus();
				}, 10);

				return;
			}
		}

		if (
			text.length > 0 &&
			pasteLongTextToFileLength > 0 &&
			text.length > pasteLongTextToFileLength
		) {
			event.preventDefault();

			const textFile = new File([text], 'Pasted', {
				type: MimeTypeText.PLAIN
			});

			onFilesAdd?.([textFile]);
		}
	}

	function handlePromptLoadStart(
		placeholderId: string,
		promptInfo: MCPPromptInfo,
		args?: Record<string, string>
	) {
		// Only clear the value if the prompt was triggered by typing '/'
		if (value.startsWith(PROMPT_TRIGGER_PREFIX)) {
			value = '';
			onValueChange?.('');
		}
		isPromptPickerOpen = false;
		promptSearchQuery = '';

		const promptName = promptInfo.title || promptInfo.name;
		const placeholder: ChatUploadedFile = {
			id: placeholderId,
			name: promptName,
			size: INITIAL_FILE_SIZE,
			type: SpecialFileType.MCP_PROMPT,
			file: new File([], 'loading'),
			isLoading: true,
			mcpPrompt: {
				serverName: promptInfo.serverName,
				promptName: promptInfo.name,
				arguments: args ? { ...args } : undefined
			}
		};

		uploadedFiles = [...uploadedFiles, placeholder];
		onUploadedFilesChange?.(uploadedFiles);
		textareaRef?.focus();
	}

	function handlePromptLoadComplete(placeholderId: string, result: GetPromptResult) {
		const promptText = result.messages
			?.map((msg: PromptMessage) => {
				if (typeof msg.content === 'string') {
					return msg.content;
				}

				if (msg.content.type === ContentPartType.TEXT) {
					return msg.content.text;
				}

				return '';
			})
			.filter(Boolean)
			.join(PROMPT_CONTENT_SEPARATOR);

		uploadedFiles = uploadedFiles.map((f) =>
			f.id === placeholderId
				? {
						...f,
						isLoading: false,
						textContent: promptText,
						size: promptText.length,
						file: new File([promptText], `${f.name}${FileExtensionText.TXT}`, {
							type: MimeTypeText.PLAIN
						})
					}
				: f
		);
		onUploadedFilesChange?.(uploadedFiles);
	}

	function handlePromptLoadError(placeholderId: string, error: string) {
		uploadedFiles = uploadedFiles.map((f) =>
			f.id === placeholderId ? { ...f, isLoading: false, loadError: error } : f
		);
		onUploadedFilesChange?.(uploadedFiles);
	}

	function handlePromptPickerClose() {
		isPromptPickerOpen = false;
		promptSearchQuery = '';
		textareaRef?.focus();
	}

	function handleInlineResourcePickerClose() {
		isInlineResourcePickerOpen = false;
		resourceSearchQuery = '';
		textareaRef?.focus();
	}

	function handleInlineResourceSelect() {
		if (value.startsWith(RESOURCE_TRIGGER_PREFIX)) {
			value = '';
			onValueChange?.('');
		}

		isInlineResourcePickerOpen = false;
		resourceSearchQuery = '';
		textareaRef?.focus();
	}

	function handleBrowseResources() {
		isInlineResourcePickerOpen = false;
		resourceSearchQuery = '';

		if (value.startsWith(RESOURCE_TRIGGER_PREFIX)) {
			value = '';
			onValueChange?.('');
		}

		isResourceDialogOpen = true;
	}

	async function handleMicClick(isAutoStop = false, hasValidSpeech = true) {
		if (!audioRecorder || !recordingSupported || isTranscribing) {
			console.warn('Audio recording not supported or transcribing in progress');
			return;
		}

		if (isRecording) {
			isRecording = false;
			cleanupSilenceDetection();

			// 手動で停止ボタンを押した場合（自動停止フラグが立っていない場合）
			if (isAutoStop !== true) {
				console.log("🔊 手動停止：音声自動対話モードをOFFにし、録音をキャンセルします。");
				try {
					audioRecorder.cancelRecording();
				} catch (e) {
					console.error(e);
				}
				
				localStorage.setItem('deining_voice_chat_enabled', 'false');
				window.dispatchEvent(new CustomEvent('deiningVoiceChatSettingsChanged'));
				isTranscribing = false;
				return;
			}

			// 自動終了だが、閾値を超えた発話が合計300ms未満（ノイズのみ）の場合は破棄
			if (!hasValidSpeech) {
				console.log("🔇 [STT VAD] 発話時間が300ms未満（ノイズのみ）のため録音を破棄します。");
				try {
					audioRecorder.cancelRecording();
				} catch (e) {
					console.error(e);
				}
				isTranscribing = false;

				// 音声自動対話モードがONなら、次の発話のために少し待って再録音を開始
				const isVoiceChat = localStorage.getItem('deining_voice_chat_enabled') === 'true';
				if (isVoiceChat && !isLoading) {
					setTimeout(() => {
						const stillVoiceChat = localStorage.getItem('deining_voice_chat_enabled') === 'true';
						if (stillVoiceChat && !isRecording && !isTranscribing && !isLoading) {
							handleMicClick();
						}
					}, 400);
				}
				return;
			}

			isTranscribing = true;

			try {
				const audioBlob = await audioRecorder.stopRecording();
				const wavBlob = await convertToWav(audioBlob);
				
				// Send WAV Blob to Flask backend for STT transcription
				const formData = new FormData();
				formData.append('file', wavBlob, 'recording.wav');

				const response = await fetch(`${getGatewayBaseUrl()}/api/stt`, {
					method: 'POST',
					body: formData
				});

				if (response.ok) {
					const data = await response.json();
					if (data.text && data.text.trim()) {
						value = value ? value + ' ' + data.text.trim() : data.text.trim();
						onValueChange?.(value);

						// 自動対話モードが有効な場合は自動送信
						const isVoiceChat = localStorage.getItem('deining_voice_chat_enabled') === 'true';
						if (isVoiceChat) {
							setTimeout(() => {
								if (value.trim()) {
									onSubmit?.();
								}
							}, 100);
						}
					}
				} else {
					console.error('STT request failed:', response.statusText);
				}
			} catch (error) {
				console.error('Failed to stop recording or transcribe:', error);
			} finally {
				isTranscribing = false;
			}
		} else {
			try {
				await audioRecorder.startRecording();
				isRecording = true;

				// 無音検知を開始
				const stream = (audioRecorder as any).stream;
				if (stream) {
					startSilenceDetection(stream);
				}
			} catch (error) {
				console.error('Failed to start recording:', error);
			}
		}
	}
</script>

<ChatFormFileInputInvisible bind:this={fileInputRef} onFileSelect={handleFileSelect} />

<form
	class="relative {className}"
	onsubmit={(event) => {
		event.preventDefault();

		if (!canSubmit || disabled || hasLoadingAttachments) return;

		onSubmit?.();
	}}
>
	<ChatFormPickers
		bind:this={pickersRef}
		{isPromptPickerOpen}
		{promptSearchQuery}
		{isInlineResourcePickerOpen}
		{resourceSearchQuery}
		onPromptPickerClose={handlePromptPickerClose}
		onInlineResourcePickerClose={handleInlineResourcePickerClose}
		onInlineResourceSelect={handleInlineResourceSelect}
		onPromptLoadStart={handlePromptLoadStart}
		onPromptLoadComplete={handlePromptLoadComplete}
		onPromptLoadError={handlePromptLoadError}
		onInlineResourceBrowse={handleBrowseResources}
	/>

	<div
		class="{INPUT_CLASSES} overflow-hidden rounded-4xl md:rounded-3xl backdrop-blur-md {disabled
			? 'cursor-not-allowed opacity-60'
			: ''}"
		data-slot="input-area"
	>
		<!-- 一体型 コンテキスト内訳ゲージバー -->
		<div class="w-full px-3 pt-1 pb-0.5 md:px-4 md:pt-2 md:pb-1 border-b border-border/20">
			<ChatScreenContextGauge
				contextTotal={currentProcessing?.contextTotal || 16384}
				contextUsed={currentProcessing?.contextUsed || 0}
				outputTokensUsed={currentProcessing?.outputTokensUsed || 0}
				outputTokensMax={currentProcessing?.outputTokensMax || -1}
				tokensPerSecond={currentProcessing?.tokensPerSecond || 0}
			/>
		</div>

		<ChatAttachmentsList
			{attachments}
			bind:uploadedFiles
			onFileRemove={handleFileRemove}
			limitToSingleRow
			class="py-5"
			style="scroll-padding: 1rem;"
			activeModelId={activeModelId ?? undefined}
		/>

		<div
			class="flex-column relative min-h-12 items-center rounded-4xl md:rounded-3xl py-2 pb-2.25 shadow-sm transition-all focus-within:shadow-md md:py-3!"
			onpaste={handlePaste}
		>
			<ChatFormTextarea
				class="px-5 py-1.5 md:pt-0"
				bind:this={textareaRef}
				bind:value
				onKeydown={handleKeydown}
				onInput={() => {
					handleInput();
					onValueChange?.(value);
				}}
				disabled={isFormDisabled}
				placeholder={computedPlaceholder}
			/>

			{#if mcpHasResourceAttachments()}
				<ChatFormMcpResourcesList
					class="mb-3"
					onResourceClick={(uri) => {
						preSelectedResourceUri = uri;
						isResourceDialogOpen = true;
					}}
				/>
			{/if}

			<ChatFormActions
				class="px-3"
				bind:this={chatFormActionsRef}
				canSend={canSubmit}
				disabled={isFormDisabled}
				{isLoading}
				isReasoning={chatStore.isReasoning}
				{isRecording}
				{showAddButton}
				{showModelSelector}
				{uploadedFiles}
				onFileUpload={handleFileUpload}
				onMicClick={handleMicClick}
				{onStop}
				onSystemPromptClick={() => onSystemPromptClick?.({ message: value, files: uploadedFiles })}
				onMcpPromptClick={showMcpPromptButton ? () => (isPromptPickerOpen = true) : undefined}
				onMcpResourcesClick={() => (isResourceDialogOpen = true)}
			/>
		</div>
	</div>
</form>

<DialogMcpResourcesBrowser
	bind:open={isResourceDialogOpen}
	preSelectedUri={preSelectedResourceUri}
	onAttach={(resource: MCPResourceInfo) => {
		mcpStore.attachResource(resource.uri);
	}}
	onOpenChange={(newOpen: boolean) => {
		if (!newOpen) {
			preSelectedResourceUri = undefined;
		}
	}}
/>
