<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import { Switch } from '$lib/components/ui/switch';
	import { Volume2, Play, Mic } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import { t } from '$lib/stores/i18n.svelte';

	interface Props {
		class?: string;
		onClose?: () => void;
	}

	let { class: className = '', onClose }: Props = $props();

	let isTtsEnabled = $state(false);
	let isVoiceChatEnabled = $state(false);
	let isSyncDisplayEnabled = $state(true);
	let ttsSpeed = $state(1.0);

	onMount(() => {
		isTtsEnabled = localStorage.getItem('deining_tts_enabled') === 'true';
		isVoiceChatEnabled = localStorage.getItem('deining_voice_chat_enabled') === 'true';
		const savedSync = localStorage.getItem('deining_tts_sync_display');
		if (savedSync !== null) {
			isSyncDisplayEnabled = savedSync === 'true';
		}
		const savedSpeed = localStorage.getItem('deining_tts_speed');
		if (savedSpeed !== null) {
			ttsSpeed = parseFloat(savedSpeed);
		}

		if (typeof window !== 'undefined') {
			(window as any).deiningTtsSpeed = ttsSpeed;
		}

		const handleSettings = () => {
			isVoiceChatEnabled = localStorage.getItem('deining_voice_chat_enabled') === 'true';
		};
		window.addEventListener('deiningVoiceChatSettingsChanged', handleSettings);

		return () => {
			window.removeEventListener('deiningVoiceChatSettingsChanged', handleSettings);
		};
	});

	$effect(() => {
		localStorage.setItem('deining_tts_enabled', isTtsEnabled ? 'true' : 'false');
	});

	$effect(() => {
		localStorage.setItem('deining_tts_sync_display', isSyncDisplayEnabled ? 'true' : 'false');
		console.log(`[TtsPanel] Sync Speech & Text Display set to: ${isSyncDisplayEnabled}`);
	});

	$effect(() => {
		localStorage.setItem('deining_voice_chat_enabled', isVoiceChatEnabled ? 'true' : 'false');
		if (typeof window !== 'undefined') {
			window.dispatchEvent(new CustomEvent('deiningVoiceChatSettingsChanged'));
		}
	});

	$effect(() => {
		localStorage.setItem('deining_tts_speed', ttsSpeed.toString());
		if (typeof window !== 'undefined') {
			(window as any).deiningTtsSpeed = ttsSpeed;
			console.log(`[TtsPanel] window.deiningTtsSpeed updated to: ${ttsSpeed}`);
		}
	});



	function handleResetSpeed() {
		ttsSpeed = 1.0;
	}

	function handleTtsTest() {
		if (typeof window !== 'undefined' && typeof (window as any).playTTS === 'function') {
			(window as any).playTTS(t('tts.testPhrase', 'This is a voice test. Are lip sync and expressions working?'));
		} else {
			console.log('Testing TTS... (playTTS not available)');
		}
	}
</script>

<div class="flex flex-col h-full {className}">
	<div class="px-4 py-3 flex items-center justify-between border-b border-border">
		<h3 class="font-semibold text-sm flex items-center gap-2">
			<Volume2 class="w-4 h-4" /> TTS & Voice Settings
		</h3>
		{#if onClose}
			<button class="text-muted-foreground hover:text-foreground p-1" onclick={onClose}>✕</button>
		{/if}
	</div>

	<div class="flex-1 overflow-y-auto p-4 flex flex-col gap-6">
		<!-- TTS Settings -->
		<div class="flex flex-col gap-3">
			<h4 class="text-xs font-semibold uppercase text-muted-foreground">Text-to-Speech</h4>
			<div class="flex items-center justify-between">
				<div class="flex items-center gap-2 text-sm">
					<Volume2 class="w-4 h-4 text-muted-foreground" /> Enable Auto TTS
				</div>
				<Switch bind:checked={isTtsEnabled} />
			</div>

			<div class="flex items-center justify-between">
				<div class="flex items-center gap-2 text-sm">
					<span class="text-xs text-muted-foreground">{t('tts.syncSpeech', 'Sync Text with Speech')}</span>
				</div>
				<Switch bind:checked={isSyncDisplayEnabled} />
			</div>
			
			<div class="flex flex-col gap-2 mt-1">
				<div class="flex items-center justify-between text-sm">
					<span class="text-xs text-muted-foreground">Speed: <span class="font-semibold text-foreground">{ttsSpeed.toFixed(1)}x</span></span>
					<button class="text-xs text-primary hover:underline" onclick={handleResetSpeed}>Reset</button>
				</div>
				<input
					type="range"
					min="0.5"
					max="2.0"
					step="0.1"
					bind:value={ttsSpeed}
					class="w-full h-2 rounded-lg appearance-none cursor-pointer bg-muted"
				/>
			</div>

			<Button size="sm" variant="secondary" onclick={handleTtsTest} class="w-full mt-2" disabled={!isTtsEnabled}>
				<Play class="w-4 h-4 mr-2" /> Test TTS Engine
			</Button>

			<div class="flex items-center justify-between mt-2 pt-2 border-t border-border/50">
				<div class="flex items-center gap-2 text-sm">
					<Mic class="w-4 h-4 text-muted-foreground" /> Voice Conversation (STT)
				</div>
				<Switch bind:checked={isVoiceChatEnabled} />
			</div>
		</div>

	</div>
</div>
