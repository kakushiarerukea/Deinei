/**
 * 統合アバターファサード (Avatar Facade)
 * 
 * window.deiningAvatar 名前空間を提供し、Live2D と VRM (3D) を統一インターフェースで制御する。
 * モデルの URL 形式 (/deining/vrm/ or .model3.json) に応じて適切なエンジンへ自動ルーティングする。
 */

import {
	initVrmViewer,
	loadVrmModel,
	applyExpression as applyVrmExpression,
	setModelVisibility as setVrmVisibility,
	setMouseTrackingMode as setVrmMouseTracking,
	invalidatePendingLoad,
	stopAnimationLoop
} from './vrm-viewer.svelte';

export interface AvatarFacade {
	mount: () => Promise<void>;
	changeModel: (url: string) => Promise<void>;
	applyExpression: (name: string, duration?: number) => void;
	isTtsPlaying: () => boolean;
	getMouthOpenValue: () => number;
	setModelVisibility: (visible: boolean) => void;
	setMouseTrackingMode: (mode: string | boolean) => void;
	setHitboxEditMode: (active: boolean) => void;
	refreshHitboxEditor: () => void;
	addHitbox: (name: string, exp: string) => void;
	changeCostume: () => void;
	isVrmActive: () => boolean;
	hideGuidance: () => void;
	deiningUploadModelZip: (file: File) => Promise<any>;
	deiningUploadVrm: (file: File) => Promise<any>;
}

let isVrm = false;

export function isLocalAccess(): boolean {
	if (typeof window === 'undefined' || !window.location) return true;
	const h = window.location.hostname.toLowerCase();
	return h === '127.0.0.1' || h === 'localhost' || h === '::1';
}

function getAvatarPref(): string {
	if (typeof window === 'undefined') return '';
	return localStorage.getItem('sys_avatar_pref') || '';
}

export function hideLive2DGuidance(): void {
	if (typeof document === 'undefined') return;
	const guideEl = document.getElementById('deining-live2d-guidance');
	if (guideEl) guideEl.style.display = 'none';
	const core = (window as any)?.deiningAvatarCore;
	if (core?.hideGuidance) core.hideGuidance();
}

export function isVrmUrl(url: string): boolean {
	return (
		url.startsWith('/deining/vrm/') ||
		url.toLowerCase().endsWith('.vrm') ||
		url.toLowerCase().endsWith('.glb')
	);
}

export async function mountAvatar(): Promise<void> {
	if (typeof window === 'undefined') return;

	const pref = getAvatarPref();
	const live2dCanvas = document.getElementById('live2d-canvas') as HTMLCanvasElement | null;
	const vrmCanvas = document.getElementById('vrm-canvas') as HTMLCanvasElement | null;

	if (isVrmUrl(pref)) {
		isVrm = true;
		hideLive2DGuidance();
		if (live2dCanvas) live2dCanvas.style.display = 'none';
		if (vrmCanvas) {
			vrmCanvas.style.display = 'block';
			await initVrmViewer(vrmCanvas);
			await loadVrmModel(pref);
		}
	} else {
		// LAN / リモートアクセス時の Live2D 描画抑止（利用規約第9条保護）
		if (!isLocalAccess()) {
			isVrm = false;
			if (vrmCanvas) vrmCanvas.style.display = 'none';
			if (live2dCanvas) live2dCanvas.style.display = 'none';
			const core = (window as any).deiningAvatarCore;
			if (core?.setLive2dActive) core.setLive2dActive(false);
			const guideEl = document.getElementById('deining-live2d-guidance');
			if (guideEl) guideEl.style.display = 'none';
			return;
		}

		isVrm = false;
		if (vrmCanvas) vrmCanvas.style.display = 'none';
		if (live2dCanvas) live2dCanvas.style.display = 'block';
		const core = (window as any).deiningAvatarCore;
		if (core?.mount) {
			await core.mount();
		} else if (core?.mountAvatarSystem) {
			await core.mountAvatarSystem();
		}
	}
}

export async function changeModel(url: string): Promise<void> {
	if (typeof window === 'undefined' || !url) return;

	const live2dCanvas = document.getElementById('live2d-canvas') as HTMLCanvasElement | null;
	const vrmCanvas = document.getElementById('vrm-canvas') as HTMLCanvasElement | null;
	const core = (window as any).deiningAvatarCore;

	if (isVrmUrl(url)) {
		// Live2D -> VRM
		isVrm = true;
		localStorage.setItem('sys_avatar_pref', url);
		hideLive2DGuidance();

		// Live2D 描画ループを停止
		if (core?.setLive2dActive) core.setLive2dActive(false);

		if (live2dCanvas) live2dCanvas.style.display = 'none';
		if (vrmCanvas) {
			vrmCanvas.style.display = 'block';
			await initVrmViewer(vrmCanvas);
			await loadVrmModel(url);
		}
	} else {
		// VRM -> Live2D
		isVrm = false;
		localStorage.setItem('sys_avatar_pref', url);

		// VRM アニメーションと描画ループを停止
		invalidatePendingLoad();
		stopAnimationLoop();

		if (vrmCanvas) vrmCanvas.style.display = 'none';

		// LAN / リモートアクセス時の Live2D 描画抑止（利用規約第9条保護）
		if (!isLocalAccess()) {
			if (live2dCanvas) live2dCanvas.style.display = 'none';
			if (core?.setLive2dActive) core.setLive2dActive(false);
			const guideEl = document.getElementById('deining-live2d-guidance');
			if (guideEl) guideEl.style.display = 'none';
			return;
		}

		if (live2dCanvas) live2dCanvas.style.display = 'block';

		// Live2D 描画ループを再開 & マウント
		if (core?.setLive2dActive) core.setLive2dActive(true);
		if (core?.mount) await core.mount();
		else if (core?.mountAvatarSystem) await core.mountAvatarSystem();
		if (core?.changeModel) core.changeModel(url);
	}
}

export function applyExpression(name: string, duration = 4000) {
	if (isVrm) {
		applyVrmExpression(name, duration);
	} else {
		const core = (window as any).deiningAvatarCore;
		if (core?.applyExpression) core.applyExpression(name);
	}
}

export function isTtsPlaying(): boolean {
	const core = (window as any).deiningAvatarCore;
	return core?.isTtsPlaying ? core.isTtsPlaying() : false;
}

export function getMouthOpenValue(): number {
	const core = (window as any).deiningAvatarCore;
	return core?.getMouthOpenValue ? core.getMouthOpenValue() : 0;
}

export function setModelVisibility(visible: boolean) {
	if (isVrm) {
		setVrmVisibility(visible);
	} else {
		const core = (window as any).deiningAvatarCore;
		if (core?.setModelVisibility) core.setModelVisibility(visible);
	}
}

export function setMouseTrackingMode(mode: string | boolean) {
	if (isVrm) {
		setVrmMouseTracking(mode);
	} else {
		const core = (window as any).deiningAvatarCore;
		if (core?.setMouseTrackingMode) core.setMouseTrackingMode(mode);
	}
}

export function setHitboxEditMode(active: boolean) {
	if (!isVrm) {
		const core = (window as any).deiningAvatarCore;
		if (core?.setHitboxEditMode) core.setHitboxEditMode(active);
	}
}

export function refreshHitboxEditor() {
	if (!isVrm) {
		const core = (window as any).deiningAvatarCore;
		if (core?.refreshHitboxEditor) core.refreshHitboxEditor();
	}
}

export function addHitbox(name: string, exp: string) {
	if (!isVrm) {
		const core = (window as any).deiningAvatarCore;
		if (core?.addHitbox) core.addHitbox(name, exp);
	}
}

export function changeCostume() {
	if (!isVrm) {
		const core = (window as any).deiningAvatarCore;
		if (core?.changeCostume) core.changeCostume();
	}
}

export function isVrmActive(): boolean {
	return isVrm;
}

export async function deiningUploadVrm(file: File): Promise<any> {
	const formData = new FormData();
	formData.append('file', file);
	const res = await fetch('/__deinei/api/avatar/vrm/upload', {
		method: 'POST',
		body: formData
	});
	if (!res.ok) {
		const errData = await res.json().catch(() => ({}));
		throw new Error(errData.error || `VRMアップロードに失敗しました (${res.status})`);
	}
	return await res.json();
}

/** グローバルな window.deiningAvatar ファサードオブジェクト */
export const deiningAvatar: AvatarFacade = {
	mount: mountAvatar,
	changeModel,
	applyExpression,
	isTtsPlaying,
	getMouthOpenValue,
	setModelVisibility,
	setMouseTrackingMode,
	setHitboxEditMode,
	refreshHitboxEditor,
	addHitbox,
	changeCostume,
	isVrmActive,
	hideGuidance: hideLive2DGuidance,
	deiningUploadModelZip: (file: File) => {
		const core = (window as any).deiningAvatarCore;
		if (core?.deiningUploadModelZip) return core.deiningUploadModelZip(file);
		throw new Error('Live2Dアップロード機能が初期化されていません');
	},
	deiningUploadVrm
};

// ブラウザ環境で window.deiningAvatar に登録
if (typeof window !== 'undefined') {
	(window as any).deiningAvatar = deiningAvatar;
}
