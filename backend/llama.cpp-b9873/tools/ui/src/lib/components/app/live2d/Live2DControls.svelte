<script lang="ts">
	import { Switch } from '$lib/components/ui/switch';
	import * as Select from '$lib/components/ui/select';
	import * as Dialog from '$lib/components/ui/dialog';
	import { MousePointer2, Settings, BoxSelect, Smile, Shirt, Eye, EyeOff, Plus, Upload, Loader2, Trash2, SlidersHorizontal, ShieldCheck } from '@lucide/svelte';
	import { onMount, onDestroy } from 'svelte';
	import { deiningAvatar, isLocalAccess, isVrmUrl } from '$lib/vrm/avatar-facade.svelte';
	import { t } from '$lib/stores/i18n.svelte';
	import { toast } from 'svelte-sonner';
	import { getGatewayBaseUrl } from '$lib/constants/api-endpoints';

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

	// モバイル環境でのオーバーレイUIタッチ連動表示制御
	let isMobileControlsVisible = $state(false);
	let mobileHideTimeout: ReturnType<typeof setTimeout> | null = null;

	function showMobileControls(keepOpenDuration = 3500) {
		isMobileControlsVisible = true;
		if (mobileHideTimeout) {
			clearTimeout(mobileHideTimeout);
			mobileHideTimeout = null;
		}
		if (isHitboxDialogOpen || isUploading || isDragging) return;

		mobileHideTimeout = setTimeout(() => {
			if (!isHitboxDialogOpen && !isUploading && !isDragging) {
				isMobileControlsVisible = false;
			}
		}, keepOpenDuration);
	}

	function toggleMobileControls() {
		if (isMobileControlsVisible) {
			if (mobileHideTimeout) {
				clearTimeout(mobileHideTimeout);
				mobileHideTimeout = null;
			}
			isMobileControlsVisible = false;
		} else {
			showMobileControls();
		}
	}

	function handleGlobalPointerDown(event: PointerEvent) {
		// モバイル画面（幅768px未満）のみ処理
		if (typeof window === 'undefined' || window.innerWidth >= 768) return;

		// チャット画面内（.chat-screen）の操作は無視（チャット入力やスクロールを阻害しない）
		const chatScreenEl = document.querySelector('.chat-screen');
		if (chatScreenEl) {
			const csRect = chatScreenEl.getBoundingClientRect();
			if (
				csRect.width > 0 && csRect.height > 0 &&
				event.clientX >= csRect.left && event.clientX <= csRect.right &&
				event.clientY >= csRect.top && event.clientY <= csRect.bottom
			) {
				return;
			}
		}

		// サイドバー、ダイアログ、ナビゲーション内での操作は無視
		const target = event.target as HTMLElement | null;
		if (target) {
			if (target.closest('header, nav, aside, [role="dialog"], #chat-sidebar, .sidebar')) {
				return;
			}
		}

		// Live2Dアバター表示領域（画面上部）に触れた場合、UIを表示
		showMobileControls(3500);
	}

	$effect(() => {
		if (typeof window !== 'undefined') {
			window.dispatchEvent(
				new CustomEvent('deiningMobileControlsChange', {
					detail: { visible: isMobileControlsVisible }
				})
			);
		}
	});

	$effect(() => {
		if (isHitboxDialogOpen) {
			isMobileControlsVisible = true;
			if (mobileHideTimeout) {
				clearTimeout(mobileHideTimeout);
				mobileHideTimeout = null;
			}
		}
	});

	interface AvatarModelItem {
		id: string;
		value: string;
		label: string;
		kind: 'vrm' | 'live2d';
		fileSize?: number;
		authors?: string[];
		license?: string;
		format?: string;
		springJoints?: number;
	}

	let isManageDialogOpen = $state(false);
	let isDeleting = $state(false);

	let models = $state<AvatarModelItem[]>([
		{ id: 'silver', value: '/deining/ulvm2_0001/ulvm2_0001.model3.json', label: '銀髪の女の子', kind: 'live2d' },
		{ id: 'brown', value: '/deining/fav_usaneko_alwb/fav_usaneko_alwb.model3.json', label: '茶髪の癒し系お姉さん', kind: 'live2d' }
	]);

	let vrmModels = $derived(models.filter(m => m.kind === 'vrm'));
	let live2dModels = $derived(models.filter(m => m.kind === 'live2d'));
	let currentModelItem = $derived(models.find(m => m.value === selectedModel));

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
			let loadedModels: AvatarModelItem[] = [];
			const res = await fetch(`${getGatewayBaseUrl()}/api/avatar/models`);
			if (res.ok) {
				const data = await res.json();
				if (Array.isArray(data.models)) {
					for (const m of data.models) {
						loadedModels.push({
							id: m.id,
							value: m.url,
							label: m.name,
							kind: 'live2d'
						});
					}
				}
			}

			// VRMモデル一覧を取得して合流
			try {
				const vrmRes = await fetch(`${getGatewayBaseUrl()}/api/avatar/vrm/list`);
				if (vrmRes.ok) {
					const vrmData = await vrmRes.json();
					if (Array.isArray(vrmData.models)) {
						for (const vm of vrmData.models) {
							loadedModels.push({
								id: vm.id,
								value: vm.url,
								label: vm.name,
								kind: 'vrm',
								fileSize: vm.fileSize,
								authors: vm.authors,
								license: vm.license,
								format: vm.format,
								springJoints: vm.springJoints
							});
						}
					}
				}
			} catch (e) { }

			if (loadedModels.length > 0) {
				models = loadedModels;
			}
		} catch (e) { }
	}

	async function deleteAvatarModel(modelId: string, modelName: string) {
		if (isDeleting) return;
		if (!confirm(`3Dモデル「${modelName}」を完全に削除しますか？`)) {
			return;
		}

		isDeleting = true;
		const toastId = toast.loading(`3Dモデル「${modelName}」を削除中...`);
		try {
			const res = await fetch(`${getGatewayBaseUrl()}/api/avatar/vrm/${encodeURIComponent(modelId)}`, {
				method: 'DELETE'
			});
			const data = await res.json();
			if (!res.ok || !data.ok) {
				throw new Error(data.error || 'モデルの削除に失敗しました');
			}

			toast.dismiss(toastId);
			toast.success(`3Dモデル「${modelName}」を削除しました`);

			const wasActive = selectedModel.includes(modelId);
			await fetchModels();

			if (wasActive) {
				const fallback = models[0]?.value || '/deining/ulvm2_0001/ulvm2_0001.model3.json';
				handleModelChange(fallback);
			}
		} catch (err: any) {
			toast.dismiss(toastId);
			toast.error(err.message || 'モデルの削除に失敗しました');
		} finally {
			isDeleting = false;
		}
	}

	async function handleAvatarUpload(file: File) {
		if (isUploading) return;

		const isVrm = file.name.toLowerCase().endsWith('.vrm');
		const isZip = file.name.toLowerCase().endsWith('.zip');

		if (!isVrm && !isZip) {
			toast.error('アバターファイル（.vrm または .zip）を指定してください');
			return;
		}

		if (isVrm && file.size > 60 * 1024 * 1024) {
			toast.error('VRMモデルのサイズ上限(60MB)を超過しています');
			return;
		}

		isUploading = true;
		uploadProgressText = `「${file.name}」を検証・配置中...`;
		const toastId = toast.loading(`アバター「${file.name}」を配置中...`);

		try {
			const formData = new FormData();
			formData.append('file', file);

			const endpoint = isVrm ? `${getGatewayBaseUrl()}/api/avatar/vrm/upload` : `${getGatewayBaseUrl()}/api/avatar/upload`;
			const res = await fetch(endpoint, {
				method: 'POST',
				body: formData
			});

			const data = await res.json();
			if (!res.ok || (!data.ok && !data.success)) {
				throw new Error(data.error || 'アバターの配置に失敗しました');
			}

			toast.dismiss(toastId);

			if (data.kind === 'vrm') {
				const registered = data.registered_models || (data.model ? [data.model] : []);
				if (registered.length > 1) {
					toast.success(`[OK] ${data.message || `${registered.length}体の3Dモデルを一括登録しました`}`, { duration: 6000 });
				} else if (data.duplicate) {
					toast.info(`[INFO] ${data.message || '既に登録済みのモデルです'}`);
				} else {
					toast.success(`[OK] 3Dモデル「${data.model?.name || file.name}」を配置しました`);
				}
			} else {
				toast.success(`[OK] Live2Dモデル「${data.model?.name || file.name}」を配置しました`);
			}

			await fetchModels();
			if (data.model && data.model.url) {
				handleModelChange(data.model.url);
			}
		} catch (err: any) {
			toast.dismiss(toastId);
			const errMsg = err.message || 'アバターのアップロードに失敗しました';
			if (errMsg.includes('対応外のLive2Dモデル')) {
				toast.error(errMsg, { duration: 9000 });
			} else {
				toast.error(errMsg);
			}
		} finally {
			isUploading = false;
			uploadProgressText = '';
			if (fileInputEl) fileInputEl.value = '';
		}
	}

	const uploadModelVrm = handleAvatarUpload;
	const uploadModelZip = handleAvatarUpload;

	function handleFileInputChange(e: Event) {
		const target = e.target as HTMLInputElement;
		if (target.files && target.files.length > 0) {
			const file = target.files[0];
			handleAvatarUpload(file);
		}
	}

	let avatarContainerEl: HTMLElement | null = null;

	function handleDragEnter(e: DragEvent) {
		e.preventDefault();
		e.stopPropagation();
		dragCounter++;
		const guideEl = document.getElementById('deining-live2d-guidance');
		const isGuidanceActive = guideEl && guideEl.style.display !== 'none';
		if (!isGuidanceActive && e.dataTransfer && e.dataTransfer.types.includes('Files')) {
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
			handleAvatarUpload(file);
		}
	}

	onMount(async () => {
		await fetchModels();

		const isRemote = !isLocalAccess();
		const savedModel = localStorage.getItem('sys_avatar_pref');
		if (savedModel) {
			const match = models.find(m => m.value === savedModel || (m.id === 'silver' && (savedModel.includes('ulvm2') || savedModel.includes('銀髪の女の子'))) || (m.id === 'brown' && (savedModel.includes('fav_usaneko') || savedModel.includes('茶髪の癒し系お姉さん'))));
			if (match) {
				selectedModel = match.value;
			}
		}

		// リモート/LAN環境でのLive2D利用規約セーフガード
		if (isRemote && !isVrmUrl(selectedModel)) {
			// VRMモデルが存在すればVRMへ自動優先切り替え
			const firstVrm = models.find(m => m.kind === 'vrm');
			if (firstVrm) {
				selectedModel = firstVrm.value;
				localStorage.setItem('sys_avatar_pref', firstVrm.value);
			} else {
				// VRMが未登録の場合はトースト通知を表示し、Live2D描画は抑止
				setTimeout(() => {
					toast.warning(
						'利用規約の制限により、LANおよびリモートアクセス環境ではLive2Dモデルをご利用いただけません。3Dアバター（.vrm）を追加・選択してご利用ください。',
						{ duration: 9000 }
					);
				}, 800);
			}
		}

		// Svelteのコンポーネント（DOM）マウント後にアバターシステムを初期化 (Live2D / VRM 自動判定)
		deiningAvatar.mount();

		// ガイダンスUIからも呼び出せるようにグローバル公開
		if (typeof window !== 'undefined') {
			(window as any).deiningUploadAvatar = handleAvatarUpload;
			(window as any).deiningUploadModelZip = handleAvatarUpload;
			(window as any).deiningUploadVrm = handleAvatarUpload;
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

		// モバイル環境でのアバター領域タッチ検知（非侵襲的にUIを表示させる）
		// 表情変更イベントのリスナー（発話タイムライン・なでなで・自動解除タイマーなどとの双方向同期）
		if (typeof window !== 'undefined') {
			window.addEventListener('pointerdown', handleGlobalPointerDown, { passive: true });
			window.addEventListener('deiningAvatarExpressionChanged', handleExternalExpressionChange);
		}
	});

	function handleExternalExpressionChange(e: Event) {
		const customEv = e as CustomEvent;
		if (customEv && customEv.detail && typeof customEv.detail.expression === 'string') {
			selectedExpression = customEv.detail.expression;
		}
	}

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			window.removeEventListener('pointerdown', handleGlobalPointerDown);
			window.removeEventListener('deiningAvatarExpressionChanged', handleExternalExpressionChange);
			delete (window as any).deiningUploadAvatar;
			delete (window as any).deiningUploadModelZip;
			delete (window as any).deiningUploadVrm;
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
		if (!isVrmUrl(value) && !isLocalAccess()) {
			toast.warning(
				'利用規約の制限により、LANおよびリモートアクセス環境ではLive2Dモデルをご利用いただけません。VRMモデルをご利用いただくか、PCローカル環境（127.0.0.1）からアクセスしてください。',
				{ duration: 8000 }
			);
		}
		if (isVrmUrl(value)) {
			deiningAvatar.hideGuidance();
		}
		deiningAvatar.changeModel(value);
	}

	function handleExpressionChange(value: string) {
		selectedExpression = value;
		deiningAvatar.applyExpression(value, value === 'idle' ? 0 : 4000);
	}

	function handleCostumeChange() {
		deiningAvatar.changeCostume();
	}

	let prevHitboxEditMode = false;
	$effect(() => {
		if (isHitboxEditMode && !prevHitboxEditMode) {
			// トグルがオンになった瞬間だけダイアログを開く
			isHitboxDialogOpen = true;
		}
		
		deiningAvatar.setHitboxEditMode(isHitboxEditMode);
		
		prevHitboxEditMode = isHitboxEditMode;
	});

	$effect(() => {
		const currentTracking = isMouseTracking;
		const currentModel = selectedModel; // アバター変更時にもトグル状態を再送信するためにトリガーさせる

		deiningAvatar.setMouseTrackingMode(currentTracking);
	});

	$effect(() => {
		const visible = isModelVisible;
		const currentModel = selectedModel; // アバター変更時にもトグル状態を再送信するためにトリガーさせる

		deiningAvatar.setModelVisibility(visible);
	});

	$effect(() => {
		if (isHitboxDialogOpen) {
			// Dialogの中身がDOMにマウントされるのを少し待ってから再描画
			setTimeout(() => {
				deiningAvatar.refreshHitboxEditor();
			}, 50);
		}
	});
</script>

<div class="absolute inset-0 pointer-events-none z-20 md:z-30 p-2 sm:p-3 md:p-4 flex flex-col justify-between">
	<!-- モバイル用 Live2D タッチ専用レイヤー (チャット領域53dvhより上のエリア) -->
	<!-- touch-action: none によりブラウザのスクロール誤認識と pointercancel を防止 -->
	<!-- button ではない通常の div なので isPointerOverChatOrUI で弾かれない -->
	<div
		class="md:hidden absolute inset-x-0 top-0 bottom-[53dvh] pointer-events-auto touch-none z-0"
		aria-hidden="true"
	></div>

	<!-- Top Bar (モバイルではヘッダーバーを避けるため mt-15 を付与、タッチ時のみ表示) -->
	<div
		class={[
			"flex items-start justify-between mt-15 md:mt-0 transition-opacity duration-300 z-20",
			isMobileControlsVisible ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none md:opacity-100 md:pointer-events-auto"
		]}
		onpointerdown={() => showMobileControls(5000)}
	>
		<!-- Left: Model Selector & Actions -->
		<div class="pointer-events-auto bg-control-panel backdrop-blur-md rounded-lg p-1.5 border border-border flex items-center gap-1.5 shadow-sm max-w-[calc(100vw-5.5rem)] md:max-w-none">
			<!-- Select Avatar (3D / 2D Grouped) -->
			<Select.Root type="single" value={selectedModel} onValueChange={handleModelChange}>
				<Select.Trigger class="h-8 min-w-[130px] max-w-[180px] sm:max-w-[210px] bg-transparent border-0 focus:ring-0 shadow-none text-xs px-2">
					<span class="truncate flex items-center gap-1.5">
						{#if currentModelItem}
							{#if currentModelItem.kind === 'vrm'}
								<span class="text-[9px] font-bold text-sky-400 bg-sky-500/15 border border-sky-400/30 px-1 py-0.5 rounded leading-none shrink-0">3D</span>
							{:else}
								<span class="text-[9px] font-bold text-amber-400 bg-amber-500/15 border border-amber-400/30 px-1 py-0.5 rounded leading-none shrink-0">2D</span>
							{/if}
							<span class="truncate">{currentModelItem.label}</span>
						{:else}
							Select Avatar
						{/if}
					</span>
				</Select.Trigger>
				<Select.Content class="min-w-[220px]">
					{#if vrmModels.length > 0}
						<Select.Group>
							<Select.GroupHeading class="text-[10px] font-bold text-sky-400 px-2 py-1 select-none flex items-center gap-1">
								<span>[3D] VRM アバター (自由追加)</span>
							</Select.GroupHeading>
							{#each vrmModels as model}
								<Select.Item value={model.value} class="text-xs">
									<span class="truncate">{model.label}</span>
								</Select.Item>
							{/each}
						</Select.Group>
					{/if}

					{#if vrmModels.length > 0 && live2dModels.length > 0}
						<Select.Separator class="my-1" />
					{/if}

					{#if live2dModels.length > 0}
						<Select.Group>
							<Select.GroupHeading class="text-[10px] font-bold text-amber-400 px-2 py-1 select-none flex items-center gap-1">
								<span>[2D] Live2D モデル (専用モデル)</span>
							</Select.GroupHeading>
							{#each live2dModels as model}
								<Select.Item value={model.value} class="text-xs">
									<span class="truncate">{model.label}</span>
								</Select.Item>
							{/each}
						</Select.Group>
					{/if}
				</Select.Content>
			</Select.Root>

			<div class="w-px h-4 bg-border/60"></div>

			<!-- アバター追加ボタン (+) -->
			<button
				type="button"
				class="h-7 w-7 rounded flex items-center justify-center hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
				title="アバターを追加 (.vrm / .zip)"
				onclick={() => fileInputEl?.click()}
				disabled={isUploading}
			>
				{#if isUploading}
					<Loader2 class="w-3.5 h-3.5 animate-spin text-primary" />
				{:else}
					<Plus class="w-3.5 h-3.5" />
				{/if}
			</button>

			<!-- アバターモデル管理ダイアログボタン -->
			<button
				type="button"
				class="h-7 w-7 rounded flex items-center justify-center hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors"
				title="アバターモデルの管理・削除"
				onclick={() => isManageDialogOpen = true}
			>
				<SlidersHorizontal class="w-3.5 h-3.5" />
			</button>

			<input
				bind:this={fileInputEl}
				type="file"
				accept=".zip,.vrm"
				class="hidden"
				onchange={handleFileInputChange}
			/>
		</div>

		<!-- Right: Toggles -->
		<div class="pointer-events-auto flex flex-col gap-1.5 md:gap-2">
			<!-- Model Visibility -->
			<div class="bg-background/50 backdrop-blur-md rounded-full p-1.5 md:p-2 border border-border shadow-sm flex items-center justify-center" title="Model Visibility">
				<Switch id="model-visibility-toggle" bind:checked={isModelVisible} class="scale-75 origin-center -my-1" />
				{#if isModelVisible}
					<Eye class="w-3.5 h-3.5 ml-1 md:ml-1.5 text-muted-foreground" />
				{:else}
					<EyeOff class="w-3.5 h-3.5 ml-1 md:ml-1.5 text-muted-foreground" />
				{/if}
			</div>

			<!-- Mouse Tracking -->
			<div class="bg-background/50 backdrop-blur-md rounded-full p-1.5 md:p-2 border border-border shadow-sm flex items-center justify-center" title="Mouse Tracking">
				<Switch id="mouse-tracking-toggle" bind:checked={isMouseTracking} class="scale-75 origin-center -my-1" />
				<MousePointer2 class="w-3.5 h-3.5 ml-1 md:ml-1.5 text-muted-foreground" />
			</div>

			<!-- Hitbox Edit Mode -->
			<div class="bg-background/50 backdrop-blur-md rounded-full p-1.5 md:p-2 border border-border shadow-sm flex items-center justify-center gap-1 {isHitboxEditMode ? 'ring-1 ring-primary' : ''}" title="Edit Hitboxes">
				<Switch id="hitbox-edit-toggle" bind:checked={isHitboxEditMode} class="scale-75 origin-center -my-1" />
				{#if isHitboxEditMode}
					<button onclick={() => isHitboxDialogOpen = true} class="ml-1 p-1 rounded-full hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors" title="Hitbox Settings">
						<Settings class="w-3.5 h-3.5" />
					</button>
				{/if}
			</div>
		</div>
	</div>

	<!-- Bottom Bar (モバイルではチャット画面(53dvh)のすぐ上に浮かせて配置、タッチ時のみ表示) -->
	<div
		class={[
			"flex items-end justify-between mb-[calc(53dvh+0.75rem)] md:mb-0 transition-opacity duration-300 z-20",
			isMobileControlsVisible ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none md:opacity-100 md:pointer-events-auto"
		]}
		onpointerdown={() => showMobileControls(5000)}
	>
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
						<button id="add-hitbox-btn" class="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2" onclick={() => deiningAvatar.addHitbox('', '')}>
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

		<!-- Avatar Manager Dialog (3D VRM 自由管理 ＆ 2D Live2D 削除保護) -->
		<Dialog.Root bind:open={isManageDialogOpen}>
			<Dialog.Content class="sm:max-w-[480px] bg-background/95 backdrop-blur-md border-border max-h-[85vh] flex flex-col">
				<Dialog.Header>
					<Dialog.Title class="flex items-center gap-2 text-foreground">
						<SlidersHorizontal class="w-5 h-5 text-primary" />
						アバターモデル管理
					</Dialog.Title>
					<Dialog.Description class="text-muted-foreground text-xs">
						登録されている3D (VRM) および 2D (Live2D) モデルの一覧と管理です
					</Dialog.Description>
				</Dialog.Header>

				<div class="space-y-4 py-2 overflow-y-auto flex-1 pr-1">
					<!-- 3D VRM Section -->
					<div>
						<div class="flex items-center justify-between mb-2">
							<h4 class="text-xs font-bold text-sky-400 flex items-center gap-1.5">
								<span class="text-[10px] bg-sky-500/15 border border-sky-400/30 px-1.5 py-0.5 rounded font-mono">3D</span>
								VRM アバター ({vrmModels.length}体)
							</h4>
							<span class="text-[11px] text-muted-foreground">自由に追加・削除可能</span>
						</div>
						{#if vrmModels.length === 0}
							<div class="p-3 rounded-lg border border-dashed border-border text-center text-xs text-muted-foreground">
								追加されたVRMモデルはありません。「+」ボタンまたは画面へのドロップで追加できます。
							</div>
						{:else}
							<div class="space-y-1.5">
								{#each vrmModels as model}
									<div class="flex items-center justify-between p-2 rounded-lg bg-card/60 border border-border hover:border-sky-500/30 transition-colors">
										<div class="min-w-0 flex-1 mr-2">
											<div class="flex items-center gap-1.5">
												<span class="text-xs font-medium text-foreground truncate">{model.label}</span>
												{#if selectedModel === model.value}
													<span class="text-[9px] bg-primary/20 text-primary border border-primary/30 px-1 py-0.2 rounded font-medium shrink-0">使用中</span>
												{/if}
											</div>
											<div class="flex items-center gap-2 text-[10px] text-muted-foreground mt-0.5">
												{#if model.format}
													<span class="font-mono uppercase">{model.format}</span>
												{/if}
												{#if model.fileSize}
													<span>{(model.fileSize / (1024 * 1024)).toFixed(1)} MB</span>
												{/if}
												{#if model.authors && model.authors.length > 0}
													<span class="truncate">作: {model.authors.join(', ')}</span>
												{/if}
											</div>
										</div>
										<div class="flex items-center gap-1 shrink-0">
											{#if selectedModel !== model.value}
												<button
													type="button"
													class="px-2 py-1 text-xs rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
													onclick={() => { handleModelChange(model.value); isManageDialogOpen = false; }}
												>
													適用
												</button>
											{/if}
											<button
												type="button"
												class="p-1.5 rounded hover:bg-destructive/15 text-muted-foreground hover:text-destructive transition-colors disabled:opacity-50"
												title={`「${model.label}」を削除`}
												disabled={isDeleting}
												onclick={() => deleteAvatarModel(model.id, model.label)}
											>
												<Trash2 class="w-3.5 h-3.5" />
											</button>
										</div>
									</div>
								{/each}
							</div>
						{/if}
					</div>

					<!-- 2D Live2D Section -->
					<div class="pt-2 border-t border-border/60">
						<div class="flex items-center justify-between mb-2">
							<h4 class="text-xs font-bold text-amber-400 flex items-center gap-1.5">
								<span class="text-[10px] bg-amber-500/15 border border-amber-400/30 px-1.5 py-0.5 rounded font-mono">2D</span>
								Live2D モデル ({live2dModels.length}体)
							</h4>
							<span class="text-[11px] text-amber-500/90 flex items-center gap-1">
								<ShieldCheck class="w-3 h-3" />
								公式専用（削除保護）
							</span>
						</div>
						<div class="space-y-1.5">
							{#each live2dModels as model}
								<div class="flex items-center justify-between p-2 rounded-lg bg-card/60 border border-border">
									<div class="min-w-0 flex-1 mr-2">
										<div class="flex items-center gap-1.5">
											<span class="text-xs font-medium text-foreground truncate">{model.label}</span>
											{#if selectedModel === model.value}
												<span class="text-[9px] bg-primary/20 text-primary border border-primary/30 px-1 py-0.2 rounded font-medium shrink-0">使用中</span>
											{/if}
										</div>
										<span class="text-[10px] text-muted-foreground">Cubism 3/4/5 公式組み込みモデル</span>
									</div>
									<div class="flex items-center gap-1 shrink-0">
										{#if selectedModel !== model.value}
											<button
												type="button"
												class="px-2 py-1 text-xs rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
												onclick={() => { handleModelChange(model.value); isManageDialogOpen = false; }}
											>
												適用
											</button>
										{/if}
										<span class="p-1.5 text-muted-foreground/40 cursor-not-allowed" title="基幹アセットのため削除保護されています">
											<ShieldCheck class="w-3.5 h-3.5" />
										</span>
									</div>
								</div>
							{/each}
						</div>
					</div>
				</div>

				<Dialog.Footer class="pt-2 border-t border-border">
					<button
						type="button"
						onclick={() => isManageDialogOpen = false}
						class="inline-flex items-center justify-center whitespace-nowrap rounded-md text-xs font-medium ring-offset-background transition-colors bg-primary text-primary-foreground hover:bg-primary/90 h-8 px-4 py-1.5 w-full"
					>
						閉じる
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
					<Select.Trigger class="h-7 w-[80px] sm:w-[95px] md:w-[100px] bg-transparent border-0 focus:ring-0 shadow-none text-xs px-1">
						{expressions.find(e => e.value === selectedExpression)?.label || "Expression"}
					</Select.Trigger>
					<Select.Content>
						{#each expressions as exp}
							<Select.Item value={exp.value}>{exp.label}</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
			</div>

			<!-- Costume Toggle (Live2Dモデル時のみ表示) -->
			{#if !currentModelItem || currentModelItem.kind === 'live2d'}
				<div class="w-px h-4 bg-border mx-1"></div>
				<button
					id="costume-toggle-btn"
					class="p-1.5 rounded hover:bg-muted/80 text-muted-foreground hover:text-foreground transition-colors"
					title="衣装切り替え (Live2D)"
					onclick={handleCostumeChange}
				>
					<Shirt class="w-4 h-4" />
				</button>
			{/if}
		</div>
	</div>

	<!-- Debug Overlay -->
	<div id="pet-debug-info" class="pointer-events-auto absolute top-20 right-4 bg-black/60 text-green-400 font-mono text-[10px] p-2 rounded w-[150px] whitespace-pre-wrap break-all" style="display: none;"></div>
</div>

<!-- ドラッグ中オーバーレイ (左ペイン限定: 3Dと2Dのデュアル案内) -->
{#if isDragging}
	<div
		class="absolute inset-3 z-[40] flex flex-col items-center justify-center bg-card/95 backdrop-blur-md border-2 border-dashed border-primary rounded-3xl animate-in fade-in duration-200 pointer-events-auto shadow-2xl p-6 text-center select-none"
		ondragover={handleDragOver}
		ondragleave={handleDragLeave}
		ondrop={handleDrop}
	>
		<div class="w-14 h-14 rounded-full bg-primary/20 border border-primary/40 flex items-center justify-center mb-3 text-primary animate-bounce">
			<Upload class="w-7 h-7" />
		</div>
		<h3 class="text-base font-bold text-foreground mb-1">アバターモデルをドロップして追加</h3>
		<p class="text-xs text-muted-foreground mb-4">
			.vrm 単体 または モデル内包 .zip を自動判別して配置します
		</p>

		<div class="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-md text-left">
			<!-- 3D VRM Card -->
			<div class="p-3 rounded-xl bg-sky-500/10 border border-sky-500/30">
				<div class="flex items-center gap-1.5 mb-1 font-bold text-xs text-sky-400">
					<span class="text-[10px] bg-sky-500/20 px-1.5 py-0.5 rounded font-mono">3D</span>
					<span>VRM アバター</span>
				</div>
				<p class="text-[11px] text-muted-foreground leading-relaxed">
					お好みの <span class="text-foreground font-medium">.vrm / .zip</span><br/>
					・自由に追加可能（複数同封も一括登録）
				</p>
			</div>

			<!-- 2D Live2D Card -->
			<div class="p-3 rounded-xl bg-amber-500/10 border border-amber-500/30">
				<div class="flex items-center gap-1.5 mb-1 font-bold text-xs text-amber-400">
					<span class="text-[10px] bg-amber-500/20 px-1.5 py-0.5 rounded font-mono">2D</span>
					<span>Live2D モデル</span>
				</div>
				<p class="text-[11px] text-muted-foreground leading-relaxed">
					<span class="text-foreground font-medium">専用2体</span> (銀髪/茶髪) のZIP<br/>
					※ 規約準拠の初期配置・復旧用
				</p>
			</div>
		</div>
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


