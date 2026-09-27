class TtsStore {
	isPlaying = $state(false);
	currentSegmentIndex = $state(0);
	isPipelineActive = $state(false);

	constructor() {
		if (typeof window !== 'undefined') {
			window.addEventListener('deiningTtsStateChanged', ((e: CustomEvent<{ isPlaying: boolean }>) => {
				this.isPlaying = !!e.detail?.isPlaying;
			}) as EventListener);
		}
	}

	stop(clearPrefetch: boolean = true) {
		if (typeof window !== 'undefined' && typeof (window as any).stopTTS === 'function') {
			(window as any).stopTTS(clearPrefetch);
		}
		this.isPlaying = false;
		this.isPipelineActive = false;
	}

	// 音声合成リクエストを送り、ブラウザで音声再生が始まった瞬間(GPU推論終了)にresolveする
	async synthesizeAndPlay(text: string): Promise<boolean> {
		if (typeof window === 'undefined') return false;
		const win = window as any;
		if (typeof win.synthesizeAndPlayTTS !== 'function') {
			if (typeof win.playTTS === 'function') {
				win.playTTS(text, true);
			}
			return true;
		}

		const res = await win.synthesizeAndPlayTTS(text, true);
		return !!res?.success;
	}

	// 現在の音声が終了するまで待機する（次の段落への移行用）
	async waitForAudioEnd(maxWaitMs: number = 15000): Promise<void> {
		if (!this.isPlaying) return;

		return new Promise((resolve) => {
			let cleanup: () => void;
			const timeout = setTimeout(() => {
				cleanup?.();
				resolve();
			}, maxWaitMs);

			const onEnded = () => {
				clearTimeout(timeout);
				cleanup?.();
				resolve();
			};

			const onStateChanged = (e: any) => {
				if (!e.detail?.isPlaying) {
					clearTimeout(timeout);
					cleanup?.();
					resolve();
				}
			};

			cleanup = () => {
				window.removeEventListener('deiningTtsSegmentEnded', onEnded);
				window.removeEventListener('deiningTtsStateChanged', onStateChanged);
			};

			window.addEventListener('deiningTtsSegmentEnded', onEnded, { once: true });
			window.addEventListener('deiningTtsStateChanged', onStateChanged);
		});
	}
}

export const ttsStore = new TtsStore();

