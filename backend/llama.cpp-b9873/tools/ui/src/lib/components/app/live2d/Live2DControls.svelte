<script lang="ts">
	import { Switch } from '$lib/components/ui/switch';
	import * as Select from '$lib/components/ui/select';
	import * as Dialog from '$lib/components/ui/dialog';
	import { MousePointer2, Settings, BoxSelect, Smile, Shirt, Eye, EyeOff, Plus, Upload, Loader2 } from '@lucide/svelte';
	import { onMount, onDestroy } from 'svelte';
	import { t } from '$lib/stores/i18n.svelte';
	import { toast } from 'svelte-sonner';

	// Live2Dの各種状態
	let isModelVisible = $state(true);
	let isMouseTracking = $state(true);
	let isHitboxEditMode = $state(false);
	let isHitboxDialogOpen = $state(false);
	let selectedModel = $state('/deining/ulvm2_0001/ulvm2_0001.model3.json');
	let selectedExpression = $state('idle');

	// モデルアップロード・ドラッグ＆ドロップ状態
	let isDragging = $state(false);
	let isUploading = $state(false);
	let uploadProgressText = $state('');
	let fileInputEl: HTMLInputElement | undefined = $state();
	let dragCounter = 0;

	let models = $state([
		{ id: 'silver', value: '/deining/ulvm2_0001/ulvm2_0001.model3.json', label: '銀髪の女の子' },
		{ id: 'brown', value: '/deining/fav_usaneko_alwb/fav_usaneko_alwb.model3.json', label: '茶髪の癒し系お姉さん' }
	]);

	const expressions = [
		{ value: 'idle', label: 'Default' },
		{ value: 'smile', label: 'Smile' },
		{ value: 'cry', label: 'Sad' },
		{ value: 'angry', label: 'Angry' },
		{ value: 'surprised', label: 'Surprise' },
		{ value: 'blush', label: 'Blush' }
	];

	async function fetchModels() {
		try {
			const res = await fetch('http://localhost:5000/api/avatar/models');
			if (res.ok) {
				const data = await res.json();
				if (Array.isArray(data.models)) {
					models = data.models.map((m: any) => ({
						id: m.id,
						value: m.url,
						label: m.name
					}));
				}
			}
		} catch (e) { }
	}

	async function uploadModelZip(file: File) {
		if (isUploading) {
			console.log('⚠️ [Deining Live2D] アップロード処理中のため、重複実行をスキップします');
			return;
		}

		if (!file.name.toLowerCase().endsWith('.zip')) {
			toast.error('Live2Dモデルは .zip 形式の圧縮ファイルを指定してください');
			return;
		}

		isUploading = true;
		uploadProgressText = `「${file.name}」を展開・配置中... 🌸`;
		const toastId = toast.loading(`Live2Dモデル「${file.name}」を配置中...`);

		try {
			const formData = new FormData();
			formData.append('file', file);

			const res = await fetch('http://localhost:5000/api/avatar/upload', {
				method: 'POST',
				body: formData
			});

			const data = await res.json();
			if (!res.ok || !data.success) {
				throw new Error(data.error || 'モデルの展開・配置に失敗しました');
			}

			toast.dismiss(toastId);
			toast.success(`🌸 Live2Dモデル「${data.model.name}」を配置しました！`);

			await fetchModels();
			if (data.model && data.model.url) {
				handleModelChange(data.model.url);
			}
		} catch (err: any) {
			toast.dismiss(toastId);
			toast.error(err.message || 'モデルのアップロードに失敗しました');
		} finally {
			isUploading = false;
			uploadProgressText = '';
			if (fileInputEl) fileInputEl.value = '';
		}
	}

	function handleFileInputChange(e: Event) {
		const target = e.target as HTMLInputElement;
		if (target.files && target.files.length > 0) {
			uploadModelZip(target.files[0]);
		}
	}

	let avatarContainerEl: HTMLElement | null = null;

	function handleDragEnter(e: DragEvent) {
		e.preventDefault();
		e.stopPropagation();
		dragCounter++;
		if (e.dataTransfer && e.dataTransfer.types.includes('Files')) {
			isDragging = true;
		}
	}

	function handleDragOver(e: DragEvent) {
		e.preventDefault();
		e.stopPropagation();
		if (e.dataTransfer) {
			e.dataTransfer.dropEffect = 'copy';
		}
	}

	function handleDragLeave(e: DragEvent) {
		e.preventDefault();
		e.stopPropagation();
		dragCounter--;
		if (dragCounter <= 0) {
			dragCounter = 0;
			isDragging = false;
		}
	}

	function handleDrop(e: DragEvent) {
		e.preventDefault();
		e.stopPropagation();
		dragCounter = 0;
		isDragging = false;

		if (isUploading) return;

		if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
			const file = e.dataTransfer.files[0];
			uploadModelZip(file);
		}
	}

	onMount(async () => {
		await fetchModels();

		const savedModel = localStorage.getItem('sys_avatar_pref');
		if (savedModel) {
			const match = models.find(m => m.value === savedModel || (m.id === 'silver' && (savedModel.includes('ulvm2') || savedModel.includes('銀髪の女の子'))) || (m.id === 'brown' && (savedModel.includes('fav_usaneko') || savedModel.includes('茶髪の癒し系お姉さん'))));
			if (match) {
				selectedModel = match.value;
			}
		}
		// Svelteのコンポーネント（DOM）がマウントされた後、Live2Dアバターシステムを初期化
		if (window.deiningAvatarCore && typeof window.deiningAvatarCore.mount === 'function') {
			window.deiningAvatarCore.mount();
		}

		// ガイダンスUI(avatar-core-v2.js)からも呼び出せるようにグローバル公開
		if (typeof window !== 'undefined') {
			(window as any).deiningUploadModelZip = uploadModelZip;
		}

		// llama.cppのチャット欄へのファイル添付機能と競合させないため、
		// window全体ではなく左ペイン(avatar-container)限定でドラッグイベントを受け付ける
		avatarContainerEl = document.getElementById('avatar-container');
		if (avatarContainerEl) {
			avatarContainerEl.addEventListener('dragenter', handleDragEnter);
			avatarContainerEl.addEventListener('dragover', handleDragOver);
			avatarContainerEl.addEventListener('dragleave', handleDragLeave);
			avatarContainerEl.addEventListener('drop', handleDrop);
		}
	});

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			if ((window as any).deiningUploadModelZip === uploadModelZip) {
				delete (window as any).deiningUploadModelZip;
			}
		}
		if (avatarContainerEl) {
			avatarContainerEl.removeEventListener('dragenter', handleDragEnter);
			avatarContainerEl.removeEventListener('dragover', handleDragOver);
			avatarContainerEl.removeEventListener('dragleave', handleDragLeave);
			avatarContainerEl.removeEventListener('drop', handleDrop);
		}
	});

	function handleModelChange(value: string) {
		selectedModel = value;
		if (window.deiningAvatarCore && typeof window.deiningAvatarCore.changeModel === 'function') {
			window.deiningAvatarCore.changeModel(value);
		}
	}

	function handleExpressionChange(value: string) {
		selectedExpression = value;
		if (window.deiningAvatarCore && typeof window.deiningAvatarCore.applyExpression === 'function') {
			window.deiningAvatarCore.applyExpression(value);
		}
	}

	function handleCostumeChange() {
		// 着せ替えイベントのトリガー
		if (window.deiningAvatarCore && typeof window.deiningAvatarCore.changeCostume === 'function') {
			window.deiningAvatarCore.changeCostume();
		}
	}

	let prevHitboxEditMode = false;
	$effect(() => {
		if (isHitboxEditMode && !prevHitboxEditMode) {
			// トグルがオンになった瞬間だけダイアログを開く
			isHitboxDialogOpen = true;
		}
		
		// 常にavatar-core.js側に現在のトグル状態を伝える
		if (window.deiningAvatarCore && typeof window.deiningAvatarCore.setHitboxEditMode === 'function') {
			window.deiningAvatarCore.setHitboxEditMode(isHitboxEditMode);
		}
		
		prevHitboxEditMode = isHitboxEditMode;
	});

	$effect(() => {
		const currentTracking = isMouseTracking;
		const currentModel = selectedModel; // アバター変更時にもトグル状態を再送信するためにトリガーさせる

		if (window.deiningAvatarCore && typeof window.deiningAvatarCore.setMouseTrackingMode === 'function') {
			window.deiningAvatarCore.setMouseTrackingMode(currentTracking);
		}
	});

	$effect(() => {
		const visible = isModelVisible;
		const currentModel = selectedModel; // アバター変更時にもトグル状態を再送信するためにトリガーさせる

		if (window.deiningAvatarCore && typeof window.deiningAvatarCore.setModelVisibility === 'function') {
			window.deiningAvatarCore.setModelVisibility(visible);
		} else {
			const canvasEl = document.getElementById('live2d-canvas');
			if (canvasEl) {
				canvasEl.style.display = visible ? 'block' : 'none';
			}
			if (window.deiningAvatarCore) {
				window.deiningAvatarCore.isModelVisible = visible;
			}
		}
	});

	$effect(() => {
		if (isHitboxDialogOpen) {
			// Dialogの中身がDOMにマウントされるのを少し待ってから再描画
			setTimeout(() => {
				if (window.deiningAvatarCore && typeof window.deiningAvatarCore.refreshHitboxEditor === 'function') {
					window.deiningAvatarCore.refreshHitboxEditor();
				}
			}, 50);
		}
	});
</script>

<div class="absolute inset-0 pointer-events-none z-30 p-4 flex flex-col justify-between">
	
	<!-- Top Bar -->
	<div class="flex items-start justify-between">
		<!-- Left: Model Selector & Import Button -->
		<div class="pointer-events-auto bg-control-panel backdrop-blur-md rounded-lg p-1.5 border border-border flex items-center gap-1.5 shadow-sm">
			<Select.Root type="single" value={selectedModel} onValueChange={handleModelChange}>
				<Select.Trigger class="h-8 w-[180px] bg-transparent border-0 focus:ring-0 shadow-none text-xs">
					{models.find(m => m.value === selectedModel)?.label || "Select Avatar"}
				</Select.Trigger>
				<Select.Content>
					{#each models as model}
						<Select.Item value={model.value}>{model.label}</Select.Item>
					{/each}
				</Select.Content>
			</Select.Root>

			<div class="w-px h-4 bg-border/60"></div>

			<!-- Live2D ZIP インポートボタン -->
			<button
				type="button"
				class="h-7 w-7 rounded flex items-center justify-center hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
				title="Live2Dモデル ZIP をインポート"
				onclick={() => fileInputEl?.click()}
				disabled={isUploading}
			>
				{#if isUploading}
					<Loader2 class="w-3.5 h-3.5 animate-spin text-primary" />
				{:else}
					<Plus class="w-3.5 h-3.5" />
				{/if}
			</button>
			<input
				bind:this={fileInputEl}
				type="file"
				accept=".zip"
				class="hidden"
				onchange={handleFileInputChange}
			/>
		</div>

		<!-- Right: Toggles -->
		<div class="pointer-events-auto flex flex-col gap-2">
			<!-- Model Visibility -->
			<div class="bg-background/50 backdrop-blur-md rounded-full p-2 border border-border shadow-sm flex items-center justify-center" title="Model Visibility">
				<Switch id="model-visibility-toggle" bind:checked={isModelVisible} class="scale-75 origin-center -my-1" />
				{#if isModelVisible}
					<Eye class="w-3.5 h-3.5 ml-1.5 text-muted-foreground" />
				{:else}
					<EyeOff class="w-3.5 h-3.5 ml-1.5 text-muted-foreground" />
				{/if}
			</div>

			<!-- Mouse Tracking -->
			<div class="bg-background/50 backdrop-blur-md rounded-full p-2 border border-border shadow-sm flex items-center justify-center" title="Mouse Tracking">
				<Switch id="mouse-tracking-toggle" bind:checked={isMouseTracking} class="scale-75 origin-center -my-1" />
				<MousePointer2 class="w-3.5 h-3.5 ml-1.5 text-muted-foreground" />
			</div>

			<!-- Hitbox Edit Mode -->
			<div class="bg-background/50 backdrop-blur-md rounded-full p-2 border border-border shadow-sm flex items-center justify-center gap-1 {isHitboxEditMode ? 'ring-1 ring-primary' : ''}" title="Edit Hitboxes">
				<Switch id="hitbox-edit-toggle" bind:checked={isHitboxEditMode} class="scale-75 origin-center -my-1" />
				{#if isHitboxEditMode}
					<button onclick={() => isHitboxDialogOpen = true} class="ml-1 p-1 rounded-full hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors" title="Hitbox Settings">
						<Settings class="w-3.5 h-3.5" />
					</button>
				{/if}
			</div>
		</div>
	</div>

	<!-- Bottom Bar -->
	<div class="flex items-end justify-between">
		<!-- Hitbox Context Menu (Dialog) -->
		<Dialog.Root bind:open={isHitboxDialogOpen}>
			<Dialog.Content class="sm:max-w-[425px] bg-background/95 backdrop-blur-md border-border">
				<Dialog.Header>
					<Dialog.Title class="flex items-center gap-2 text-foreground">
						<BoxSelect class="w-5 h-5"/> Hitbox Editor
					</Dialog.Title>
					<Dialog.Description class="text-muted-foreground">
						{t('live2d.editTouchZones', 'Edit touch interaction zones')}
					</Dialog.Description>
				</Dialog.Header>

				<!-- 泥濘: 動的追加先のコンテナ -->
				<div class="custom-hitbox-styles py-4 pointer-events-auto">
					<div class="flex justify-end mb-4">
						<button id="add-hitbox-btn" class="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2" onclick={() => window.deiningAvatarCore?.addHitbox?.()}>
							Add New Hitbox
						</button>
					</div>
					<!-- ここに avatar-core.js が動的に生成したUIをマウントする -->
					<div id="hitbox-list-container" class="space-y-4 max-h-[400px] overflow-y-auto pr-2">
						<!-- 初期ロードまでのプレースホルダー -->
						<div class="text-center text-muted-foreground text-sm py-8">
							Loading hitboxes...
						</div>
					</div>
				</div>

				<Dialog.Footer>
					<button onclick={() => isHitboxDialogOpen = false} class="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground hover:bg-primary/90 h-10 px-4 py-2 w-full">
						Close
					</button>
				</Dialog.Footer>
			</Dialog.Content>
		</Dialog.Root>

		<div class="flex-1"></div>

		<!-- Right: Quick Actions -->
		<div class="pointer-events-auto flex items-center gap-2 bg-control-panel backdrop-blur-md rounded-lg p-1.5 border border-border shadow-sm">
			<!-- Expressions -->
			<div class="flex items-center bg-control-inner rounded px-1.5">
				<Smile class="w-3.5 h-3.5 text-muted-foreground mr-1.5" />
				<Select.Root type="single" value={selectedExpression} onValueChange={handleExpressionChange}>
					<Select.Trigger class="h-7 w-[100px] bg-transparent border-0 focus:ring-0 shadow-none text-xs px-1">
						{expressions.find(e => e.value === selectedExpression)?.label || "Expression"}
					</Select.Trigger>
					<Select.Content>
						{#each expressions as exp}
							<Select.Item value={exp.value}>{exp.label}</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
			</div>

			<div class="w-px h-4 bg-border mx-1"></div>

			<!-- Costume Toggle -->
			<button
				id="costume-toggle-btn"
				class="p-1.5 rounded hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors"
				title="Change Costume"
				onclick={handleCostumeChange}
			>
				<Shirt class="w-4 h-4" />
			</button>
		</div>
	</div>

	<!-- Debug Overlay -->
	<div id="pet-debug-info" class="pointer-events-auto absolute top-20 right-4 bg-black/60 text-green-400 font-mono text-[10px] p-2 rounded w-[150px] whitespace-pre-wrap break-all" style="display: none;"></div>
</div>

<!-- ドラッグ中オーバーレイ (左ペイン限定) -->
{#if isDragging}
	<div
		class="absolute inset-3 z-[40] flex flex-col items-center justify-center bg-card/95 backdrop-blur-md border-2 border-dashed border-primary rounded-3xl animate-in fade-in duration-200 pointer-events-auto shadow-2xl p-6 text-center select-none"
		ondragover={handleDragOver}
		ondragleave={handleDragLeave}
		ondrop={handleDrop}
	>
		<div class="w-16 h-16 rounded-full bg-primary/20 border border-primary/40 flex items-center justify-center mb-4 text-primary animate-bounce">
			<Upload class="w-8 h-8" />
		</div>
		<h3 class="text-base font-bold text-foreground mb-1.5">Live2Dモデル ZIP をドロップ</h3>
		<p class="text-xs text-muted-foreground leading-relaxed mb-3">
			「銀髪の女の子」または「茶髪の癒し系お姉さん」のモデルZIPを配置します
		</p>
		<span class="inline-flex items-center gap-1.5 text-[11px] text-primary bg-primary/10 px-3 py-1 rounded-full border border-primary/30">
			🌸 専用モデル対応
		</span>
	</div>
{/if}

<!-- 展開・アップロード中ローディングオーバーレイ (左ペイン限定) -->
{#if isUploading}
	<div class="absolute inset-3 z-[45] flex flex-col items-center justify-center bg-background/95 backdrop-blur-md border border-primary/40 rounded-3xl animate-in fade-in duration-200 pointer-events-auto p-6 text-center shadow-2xl select-none">
		<div class="w-12 h-12 rounded-full border-4 border-primary border-t-transparent animate-spin mb-3"></div>
		<h3 class="text-base font-bold text-foreground mb-1.5">モデルを展開・配置中...</h3>
		<p class="text-xs text-muted-foreground leading-relaxed">{uploadProgressText}</p>
	</div>
{/if}


