/**
 * VRM 3D モデル描画・制御エンジン
 * 
 * Three.js + @pixiv/three-vrm を動的インポート (dynamic import) して初期化・描画。
 * VRM 未使用時はメインバンドルに影響せず、VRM 選択時のみ遅延ロードされる。
 * 待機モーションは手続き的アイドル (脱力リラックスポーズ + 指の自然なカーブ + 呼吸) で制御。
 */

import { resolveVrmExpression } from './vrm-expression-timeline';

let THREE: any = null;
let VRMLoaderPlugin: any = null;
let VRMUtils: any = null;
let GLTFLoader: any = null;

let canvas: HTMLCanvasElement | null = null;
let renderer: any = null;
let scene: any = null;
let camera: any = null;
let currentVrm: any = null;
let animFrameId: number | null = null;
let lastTime = 0;

let isVisible = true;
let isMouseTracking = true;
let mouseTarget = { x: 0, y: 0 };

// ロード世代管理 (競合ロードの破棄ガード)
let loadGeneration = 0;

/** 未完了のロード世代を無効化 */
export function invalidatePendingLoad() {
	loadGeneration++;
}

// 視線追従 (head 直接制御 + lookAt.target 併用)
let gazeTarget: any = null;         // scene 追加済み Object3D (1度だけ生成し再利用)
let gazeSmX = 0, gazeSmY = 0;       // 平滑化後マウス (-1..1)
let headNode: any = null;           // currentVrm の head normalized bone
let gazePitchSign = 1;              // ロード時計測 (VRM0 の 180 度回転等で反転し得る)
const GAZE_HEAD_YAW_MAX = 0.35;     // rad: head yaw 上限 (約 20 度)
const GAZE_HEAD_PITCH_MAX = 0.17;   // rad: head pitch 上限 (約 10 度)

// 手続き的アイドル (腕の脱力リラックス + 肘の前方自然屈曲 + 肩のリラックス + 指のカーブ + 呼吸)
interface ArmSigns {
	leftUpper: number;
	rightUpper: number;
	leftLowerFlex: number;     // Y軸: 肘の前方屈曲符号 (+Z方向)
	rightLowerFlex: number;    // Y軸: 肘の前方屈曲符号 (+Z方向)
	leftLowerCoronal: number;  // Z軸: 前腕の体側微調整符号
	rightLowerCoronal: number; // Z軸: 前腕の体側微調整符号
}
let armSigns: ArmSigns | null = null;
const IDLE_SHOULDER_Z = 0.04;        // rad: 肩(鎖骨)の自然な脱力下げ (約2.3度、緊張をほぐす)
const IDLE_UPPER_ARM_Z = 1.25;       // rad: 腕下ろし角度 (約71.6度、体に自然に沿わせる)
const IDLE_UPPER_ARM_X = 0.08;       // rad: 上腕をわずかに前へ
const IDLE_UPPER_ARM_Y = 0.16;       // rad: 上腕の内旋 (肘を自然に斜め後ろへ)
const IDLE_LOWER_ARM_FLEX = 0.32;    // rad: 肘の自然な前方屈曲 (約18.3度、手先が前にふんわり出る)
const IDLE_LOWER_ARM_CORONAL = 0.06; // rad: 肘の過剰な外張りを防ぐ体側微調整 (約3.4度)

// 表情タイマー管理
let currentExpression = 'neutral';
let expressionTimer: any = null;
// 非対応プリセットの警告履歴 (プリセットごとに 1 回だけ出すための記録)
const warnedUnsupportedExpressions = new Set<string>();

// まばたき管理
let nextBlinkTime = 0;
let isBlinking = false;
let blinkStartTime = 0;

/** ポインター移動リスナー (視線追従用マウス座標の自動追従) */
function onPointerMove(event: PointerEvent) {
	if (!isVisible || !isMouseTracking) return;

	if (canvas) {
		const rect = canvas.getBoundingClientRect();
		// 顔の高さ付近 (キャンバス中心X、上から35%位置) を視線の中心基準とする
		const faceX = rect.left + rect.width * 0.5;
		const faceY = rect.top + rect.height * 0.35;
		const nx = Math.max(-1.0, Math.min(1.0, (event.clientX - faceX) / (rect.width * 0.5)));
		const ny = Math.max(-1.0, Math.min(1.0, -(event.clientY - faceY) / (rect.height * 0.5)));
		updateMouseTarget(nx, ny);
	} else {
		const nx = Math.max(-1.0, Math.min(1.0, (event.clientX / window.innerWidth) * 2 - 1));
		const ny = Math.max(-1.0, Math.min(1.0, -((event.clientY / window.innerHeight) * 2 - 1)));
		updateMouseTarget(nx, ny);
	}
}

/** 動的インポートで Three.js と Three-VRM をロード */
async function loadLibraries() {
	if (!THREE) {
		THREE = await import('three');
		const vrmModule = await import('@pixiv/three-vrm');
		VRMLoaderPlugin = vrmModule.VRMLoaderPlugin;
		VRMUtils = vrmModule.VRMUtils;
		const gltfModule = await import('three/addons/loaders/GLTFLoader.js');
		GLTFLoader = gltfModule.GLTFLoader;
	}
}

/** head pitch の回転符号を計測 (顔が上を向く回転方向) */
function probeGazePitchSign(vrm: any, head: any): number {
	if (!head || !vrm?.humanoid || !THREE) return 1;
	const leftEye = vrm.humanoid.getNormalizedBoneNode('leftEye');
	const rightEye = vrm.humanoid.getNormalizedBoneNode('rightEye');
	const eye = leftEye || rightEye || head;
	const p0 = new THREE.Vector3();
	eye.getWorldPosition(p0);

	head.rotation.x = 0.2;
	vrm.humanoid.update();
	const p1 = new THREE.Vector3();
	eye.getWorldPosition(p1);

	head.rotation.x = 0;
	vrm.humanoid.update();

	// 目位置の Y 変化が判定閾値未満 (目ボーン不在の head フォールバック等) は既定 +1 で警告のみ
	const dY = p1.y - p0.y;
	if (Math.abs(dY) < 0.0015) {
		console.warn(`[VRM] gaze pitch sign ambiguous (dY=${dY.toFixed(4)}, fallback +1)`);
		return 1;
	}
	return dY > 0 ? 1 : -1;
}

/** 腕を下ろすためのボーン回転符号を判定 */
function probeArmSign(
	vrm: any,
	bone: any,
	handBone: any,
	angle: number
): number {
	if (!bone || !handBone || !vrm?.humanoid || !THREE) return 1;
	const p0 = new THREE.Vector3();
	handBone.getWorldPosition(p0);

	// +angle 試行
	bone.rotation.z = angle;
	vrm.humanoid.update();
	const pPlus = new THREE.Vector3();
	handBone.getWorldPosition(pPlus);

	// -angle 試行
	bone.rotation.z = -angle;
	vrm.humanoid.update();
	const pMinus = new THREE.Vector3();
	handBone.getWorldPosition(pMinus);

	bone.rotation.z = 0;
	vrm.humanoid.update();

	if (pPlus.y < p0.y && pMinus.y >= p0.y) return 1;
	if (pMinus.y < p0.y && pPlus.y >= p0.y) return -1;
	if (pPlus.y >= p0.y && pMinus.y >= p0.y) {
		// A ポーズリグ等で両方向とも手が上がる場合のフォールバック
		console.warn('[VRM] idle pose: both arm signs raise hand (A-pose rig assumed)');
	}
	return pPlus.y <= pMinus.y ? 1 : -1;
}

/** 肘を前方に自然屈曲させるためのボーン回転符号(Y軸)を判定 (+Z方向への変位) */
function probeElbowFlexSign(
	vrm: any,
	lowerArm: any,
	hand: any,
	angle = 0.3
): number {
	if (!lowerArm || !hand || !vrm?.humanoid || !THREE) return 1;
	const p0 = new THREE.Vector3();
	hand.getWorldPosition(p0);

	lowerArm.rotation.y = angle;
	vrm.humanoid.update();
	const pPlus = new THREE.Vector3();
	hand.getWorldPosition(pPlus);

	lowerArm.rotation.y = -angle;
	vrm.humanoid.update();
	const pMinus = new THREE.Vector3();
	hand.getWorldPosition(pMinus);

	lowerArm.rotation.y = 0;
	vrm.humanoid.update();

	return pPlus.z >= pMinus.z ? 1 : -1;
}

/** 指と手首を自然な脱力・リラックスカーブに整える */
function relaxFingers(hum: any) {
	if (!hum) return;

	const fingers = ['Index', 'Middle', 'Ring', 'Little'];
	for (const side of ['left', 'right']) {
		const sign = side === 'left' ? 1 : -1;

		// 4本指 (人差し指〜小指): 自然な脱力カーブ
		for (const f of fingers) {
			const p = hum.getNormalizedBoneNode(`${side}${f}Proximal`);
			const i = hum.getNormalizedBoneNode(`${side}${f}Intermediate`);
			const d = hum.getNormalizedBoneNode(`${side}${f}Distal`);

			// 小指に向かって少し曲がりを深くする
			const flex = f === 'Little' ? 1.25 : (f === 'Ring' ? 1.15 : (f === 'Middle' ? 1.0 : 0.9));

			// Z軸屈曲 (手のひら側へ軽く丸める)
			if (p) p.rotation.z = sign * 0.28 * flex;
			if (i) i.rotation.z = sign * 0.48 * flex;
			if (d) d.rotation.z = sign * 0.28 * flex;
		}

		// 親指 (内側へ自然に寄せて丸める)
		const tMeta = hum.getNormalizedBoneNode(`${side}ThumbMetacarpal`);
		const tProx = hum.getNormalizedBoneNode(`${side}ThumbProximal`);
		const tDist = hum.getNormalizedBoneNode(`${side}ThumbDistal`);
		if (tMeta) {
			tMeta.rotation.y = sign * -0.15;
			tMeta.rotation.z = sign * 0.15;
		}
		if (tProx) {
			tProx.rotation.z = sign * 0.22;
		}
		if (tDist) {
			tDist.rotation.z = sign * 0.25;
		}

		// 手首 (Hand): わずかに内側・下へ脱力
		const hand = hum.getNormalizedBoneNode(`${side}Hand`);
		if (hand) {
			hand.rotation.z = sign * 0.08;
			hand.rotation.x = 0.05;
		}
	}
}

/** 手続き的アイドルの基本ポーズ確立 (ロード成功時に1回実行) */
function setupIdlePose(vrm: any) {
	if (!vrm?.humanoid || !THREE) return;
	const hum = vrm.humanoid;
	const lSh = hum.getNormalizedBoneNode('leftShoulder');
	const rSh = hum.getNormalizedBoneNode('rightShoulder');
	const lUA = hum.getNormalizedBoneNode('leftUpperArm');
	const rUA = hum.getNormalizedBoneNode('rightUpperArm');
	const lLA = hum.getNormalizedBoneNode('leftLowerArm');
	const rLA = hum.getNormalizedBoneNode('rightLowerArm');
	const lHand = hum.getNormalizedBoneNode('leftHand');
	const rHand = hum.getNormalizedBoneNode('rightHand');

	// 肩(鎖骨)の脱力リラックス (緊張感をなくしなだらかに下げる)
	if (lSh) lSh.rotation.z = IDLE_SHOULDER_Z;
	if (rSh) rSh.rotation.z = -IDLE_SHOULDER_Z;

	// 上腕符号判定
	const leftUpperSign = lUA && lHand ? probeArmSign(vrm, lUA, lHand, IDLE_UPPER_ARM_Z) : 1;
	const rightUpperSign = rUA && rHand ? probeArmSign(vrm, rUA, rHand, IDLE_UPPER_ARM_Z) : -1;

	// 上腕ポーズ適用
	if (lUA) {
		lUA.rotation.set(IDLE_UPPER_ARM_X, -IDLE_UPPER_ARM_Y, leftUpperSign * IDLE_UPPER_ARM_Z);
	}
	if (rUA) {
		rUA.rotation.set(IDLE_UPPER_ARM_X, IDLE_UPPER_ARM_Y, rightUpperSign * IDLE_UPPER_ARM_Z);
	}
	vrm.humanoid.update();

	// 肘の前方屈曲符号判定 (Y軸)
	const leftLowerFlex = lLA && lHand ? probeElbowFlexSign(vrm, lLA, lHand, 0.3) : 1;
	const rightLowerFlex = rLA && rHand ? probeElbowFlexSign(vrm, rLA, rHand, 0.3) : -1;

	const leftLowerCoronal = leftUpperSign;
	const rightLowerCoronal = rightUpperSign;

	// 肘の自然な脱力屈曲 (前方屈曲 Y + 体側微調整 Z)
	if (lLA) lLA.rotation.set(0, leftLowerFlex * IDLE_LOWER_ARM_FLEX, leftLowerCoronal * IDLE_LOWER_ARM_CORONAL);
	if (rLA) rLA.rotation.set(0, rightLowerFlex * IDLE_LOWER_ARM_FLEX, rightLowerCoronal * IDLE_LOWER_ARM_CORONAL);

	// 指と手首のリラックスカーブ適用
	relaxFingers(hum);

	vrm.humanoid.update();

	armSigns = {
		leftUpper: leftUpperSign,
		rightUpper: rightUpperSign,
		leftLowerFlex,
		rightLowerFlex,
		leftLowerCoronal,
		rightLowerCoronal
	};
}

/** 初期化: Canvas に Three.js レンダラーとシーンをバインド */
export async function initVrmViewer(targetCanvas: HTMLCanvasElement) {
	await loadLibraries();

	canvas = targetCanvas;

	const width = canvas.clientWidth || 300;
	const height = canvas.clientHeight || 400;

	if (!renderer) {
		renderer = new THREE.WebGLRenderer({
			canvas,
			alpha: true,
			antialias: true
		});
		renderer.setSize(width, height);
		renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
		renderer.outputColorSpace = THREE.SRGBColorSpace;
	}

	if (!scene) {
		scene = new THREE.Scene();

		// ライティング設定
		const ambientLight = new THREE.AmbientLight(0xffffff, 1.2);
		scene.add(ambientLight);

		const dirLight = new THREE.DirectionalLight(0xffffff, 1.0);
		dirLight.position.set(0, 2, 2);
		scene.add(dirLight);
	}

	if (!camera) {
		camera = new THREE.PerspectiveCamera(30, width / height, 0.1, 20.0);
		camera.position.set(0, 1.35, 1.45);
	}

	// 再初期化時に同一リスナが重複登録されないよう、追加前に peel しておく
	window.removeEventListener('resize', resizeVrmViewer);
	window.removeEventListener('pointermove', onPointerMove);
	window.addEventListener('resize', resizeVrmViewer);
	window.addEventListener('pointermove', onPointerMove, { passive: true });
	startAnimationLoop();
}

/** リサイズ追従 */
export function resizeVrmViewer() {
	if (!canvas || !renderer || !camera) return;
	const width = canvas.clientWidth || 300;
	const height = canvas.clientHeight || 400;
	camera.aspect = width / height;
	camera.updateProjectionMatrix();
	renderer.setSize(width, height);
}

/** モデルのロード */
export async function loadVrmModel(url: string) {
	await loadLibraries();
	if (!scene) return;

	const gen = ++loadGeneration;

	// 既存モデルの解放
	if (currentVrm) {
		scene.remove(currentVrm.scene);
		if (VRMUtils?.deepDispose) {
			VRMUtils.deepDispose(currentVrm.scene);
		}
		currentVrm = null;
		headNode = null;
		armSigns = null;
	}

	const loader = new GLTFLoader();
	loader.register((parser: any) => new VRMLoaderPlugin(parser));

	try {
		const gltf = await loader.loadAsync(url);
		if (gen !== loadGeneration) {
			// ロード中に別のモデル要求があった場合は破棄
			if (gltf.userData.vrm && VRMUtils?.deepDispose) {
				VRMUtils.deepDispose(gltf.userData.vrm.scene);
			}
			return;
		}

		const vrm = gltf.userData.vrm;
		if (!vrm) {
			throw new Error('VRM data not found in file');
		}

		// VRM0 の正規化対応
		if (VRMUtils?.rotateVRM0) {
			VRMUtils.rotateVRM0(vrm);
		}
		// 不要な頂点・ボーンの整理
		if (VRMUtils?.removeUnnecessaryVertices) {
			VRMUtils.removeUnnecessaryVertices(gltf.scene);
		}
		if (VRMUtils?.removeUnnecessaryJoints) {
			VRMUtils.removeUnnecessaryJoints(gltf.scene);
		}

		currentVrm = vrm;
		// [DEBUG] headless 検証・診断用の参照ハンドル (PoC が window.__vrm を参照)。
		// disposeVrmViewer 後も破棄済みオブジェクトを指したままになる (診断専用の合意、製品機能ではない)
		(window as any).__vrm = vrm;
		scene.add(vrm.scene);

		// head ボーンと視線追従のセットアップ
		if (vrm.humanoid) {
			headNode = vrm.humanoid.getNormalizedBoneNode('head') || null;
			gazePitchSign = probeGazePitchSign(vrm, headNode);

			// カメラの自動フィット (頭部位置基準)
			if (headNode) {
				const headPos = new THREE.Vector3();
				headNode.getWorldPosition(headPos);
				camera.position.set(0, headPos.y + 0.05, 1.45);
				camera.lookAt(0, headPos.y - 0.05, 0);
			}

			// 手続き的アイドルの基本ポーズをセットアップ
			setupIdlePose(vrm);
		}

		// LookAt ターゲットオブジェクトの設定
		if (!gazeTarget) {
			gazeTarget = new THREE.Object3D();
		}
		if (gazeTarget.parent !== scene) {
			scene.add(gazeTarget);
		}
		gazeTarget.position.set(0, 1.35, 3.0);

		if (vrm.lookAt) {
			vrm.lookAt.target = gazeTarget;
			vrm.lookAt.autoUpdate = true;
		}

		currentExpression = 'neutral';
		if (vrm.expressionManager) {
			vrm.expressionManager.setValue('neutral', 1.0);
		}

		console.log(`[VRM] Loaded successfully: ${vrm.meta?.name || url}`);
	} catch (err) {
		console.error('[VRM] Failed to load model:', err);
	}
}

/** 表情の適用 (一定時間後に自動で平常顔に戻す) */
export function applyExpression(name: string, durationMs = 4000) {
	if (!currentVrm || !currentVrm.expressionManager) return;
	const em = currentVrm.expressionManager;

	const targetExp = resolveVrmExpression(name);

	// 未登録プリセットは three-vrm の setValue が黙って無視するため、プリセットごとに 1 回だけ警告を出す
	// (感情タイムラインから繰り返し呼ばれるため連発を回避)
	if (!em.expressions.some((exp: any) => exp.expressionName === targetExp)) {
		if (!warnedUnsupportedExpressions.has(targetExp)) {
			warnedUnsupportedExpressions.add(targetExp);
			console.warn(`[VRM] expression not supported by model: ${targetExp}`);
		}
	}

	if (expressionTimer) {
		clearTimeout(expressionTimer);
		expressionTimer = null;
	}

	// 現在の表情をリセット (リップシンクの 'aa' と まばたきの 'blink' は保護)
	// 注: lookUp/lookDown/lookLeft/lookRight morph には直接書き込まないためリセットも衝突なし
	for (const exp of em.expressions) {
		if (exp.expressionName !== 'aa' && exp.expressionName !== 'blink') {
			em.setValue(exp.expressionName, 0);
		}
	}

	em.setValue(targetExp, 1.0);
	currentExpression = targetExp;

	if (targetExp !== 'neutral' && durationMs > 0) {
		expressionTimer = setTimeout(() => {
			if (currentVrm && currentVrm.expressionManager) {
				currentVrm.expressionManager.setValue(targetExp, 0);
				currentVrm.expressionManager.setValue('neutral', 1.0);
				currentExpression = 'neutral';
			}
			expressionTimer = null;
		}, durationMs);
	}
}

/** 表示/非表示の切り替え */
export function setModelVisibility(visible: boolean) {
	isVisible = visible;
	if (canvas) {
		canvas.style.display = visible ? 'block' : 'none';
	}
}

/** マウス追従モードの設定 */
export function setMouseTrackingMode(mode: string | boolean) {
	if (typeof mode === 'boolean') {
		isMouseTracking = mode;
	} else {
		isMouseTracking = mode !== 'off' && mode !== 'none';
	}
	if (!isMouseTracking) {
		// OFF 時は平滑化値を即時リセットし、正面固定にする
		gazeSmX = 0;
		gazeSmY = 0;
		if (gazeTarget) {
			gazeTarget.position.set(0, 1.35, 3.0);
		}
	}
}

/** マウス座標の更新 (正規化座標 -1.0 〜 +1.0) */
export function updateMouseTarget(x: number, y: number) {
	mouseTarget.x = x;
	mouseTarget.y = y;
}

/** アニメーションループの開始 */
export function startAnimationLoop() {
	if (animFrameId !== null) return;
	lastTime = performance.now();

	const renderLoop = (time: number) => {
		animFrameId = requestAnimationFrame(renderLoop);

		const rawDelta = (time - lastTime) / 1000.0;
		lastTime = time;

		if (!isVisible || !renderer || !scene || !camera) return;

		// 生 delta が 0.5 秒を超えたら不連続スキップ
		const isDeltaDiscontinuous = rawDelta > 0.5;
		const delta = isDeltaDiscontinuous ? 0 : rawDelta;
		const sec = time / 1000.0;

		if (currentVrm) {
			const em = currentVrm.expressionManager;
			const hum = currentVrm.humanoid;

			// 1. リップシンク (TTS音声振幅のサンプリング)
			if (em) {
				const getMouth = (window as any).deiningAvatarCore?.getMouthOpenValue;
				const mouthVal = typeof getMouth === 'function' ? getMouth() : 0;
				em.setValue('aa', Math.min(1.0, mouthVal * 1.2));
			}

			// 2. まばたき (平常顔 neutral の時のみ自然な自動瞬きを実行し、表情発動中は抑止)
			if (em) {
				const isNeutral = currentExpression === 'neutral';
				if (!isNeutral) {
					isBlinking = false;
					em.setValue('blink', 0);
					nextBlinkTime = time + 2000 + Math.random() * 3000;
				} else {
					if (!isBlinking && time > nextBlinkTime) {
						isBlinking = true;
						blinkStartTime = time;
					}
					if (isBlinking) {
						const elapsed = time - blinkStartTime;
						const blinkDuration = 140; // 瞬きの所要時間 ms
						if (elapsed < blinkDuration) {
							const progress = elapsed / blinkDuration;
							// 目を閉じる→開く
							const val = progress < 0.5 ? progress * 2 : (1.0 - progress) * 2;
							em.setValue('blink', val);
						} else {
							em.setValue('blink', 0);
							isBlinking = false;
							// 次のまばたきまでの間隔: 2.5〜6.5秒
							nextBlinkTime = time + 2500 + Math.random() * 4000;
						}
					}
				}
			}

			// 3. 手続き的アイドル (腕の微振動 + 胸・背骨の呼吸)
			if (hum) {
				// 腕の微小な揺らぎ (振幅約 0.8度)
				if (armSigns) {
					const lUA = hum.getNormalizedBoneNode('leftUpperArm');
					const rUA = hum.getNormalizedBoneNode('rightUpperArm');
					const armSway = Math.sin(sec * 0.7) * 0.015;

					if (lUA) {
						lUA.rotation.set(
							IDLE_UPPER_ARM_X,
							-IDLE_UPPER_ARM_Y,
							armSigns.leftUpper * (IDLE_UPPER_ARM_Z + armSway)
						);
					}
					if (rUA) {
						rUA.rotation.set(
							IDLE_UPPER_ARM_X,
							IDLE_UPPER_ARM_Y,
							armSigns.rightUpper * (IDLE_UPPER_ARM_Z + armSway)
						);
					}
					const lLA = hum.getNormalizedBoneNode('leftLowerArm');
					const rLA = hum.getNormalizedBoneNode('rightLowerArm');
					if (lLA) {
						lLA.rotation.set(
							0,
							armSigns.leftLowerFlex * IDLE_LOWER_ARM_FLEX,
							armSigns.leftLowerCoronal * IDLE_LOWER_ARM_CORONAL
						);
					}
					if (rLA) {
						rLA.rotation.set(
							0,
							armSigns.rightLowerFlex * IDLE_LOWER_ARM_FLEX,
							armSigns.rightLowerCoronal * IDLE_LOWER_ARM_CORONAL
						);
					}
				}

				// 呼吸 (周期4秒、胸と背骨の連動)
				const breath = Math.sin((sec * 2 * Math.PI) / 4.0) * 0.015;
				const spine = hum.getNormalizedBoneNode('spine');
				const chest = hum.getNormalizedBoneNode('chest');
				if (spine) spine.rotation.x = breath;
				if (chest) chest.rotation.x = breath * 0.7;
			}

			// 4. 目線追従 & 頭部回転 (LookAt)
			const k = 1 - Math.exp(-delta * 10);
			if (isMouseTracking) {
				gazeSmX += (mouseTarget.x - gazeSmX) * k;
				gazeSmY += (mouseTarget.y - gazeSmY) * k;

				const headYaw = gazeSmX * GAZE_HEAD_YAW_MAX + Math.sin(sec * 0.6) * 0.012;
				const headPitch = (gazeSmY * GAZE_HEAD_PITCH_MAX * gazePitchSign) + Math.sin(sec * 0.4 + 1.3) * 0.006;
				if (headNode) {
					headNode.rotation.set(headPitch, headYaw, 0);
				}
				if (gazeTarget) {
					gazeTarget.position.set(gazeSmX * 0.8, 1.35 + gazeSmY * 0.4, 3.0);
				}
			} else {
				// OFF 時: sway のみ
				gazeSmX = 0;
				gazeSmY = 0;
				const swayYaw = Math.sin(sec * 0.6) * 0.012;
				const swayPitch = Math.sin(sec * 0.4 + 1.3) * 0.006;
				if (headNode) {
					headNode.rotation.set(swayPitch, swayYaw, 0);
				}
				if (gazeTarget) {
					gazeTarget.position.set(0, 1.35, 3.0);
				}
			}

			// 5. ボーンの正規化姿勢更新
			if (hum) {
				hum.update();
			}

			// 6. VRM 全体更新 (LookAt target 自動適用、表情、スプリングボーン物理演算)
			if (!isDeltaDiscontinuous) {
				currentVrm.update(delta);
			}
		}

		renderer.render(scene, camera);
	};

	animFrameId = requestAnimationFrame(renderLoop);
}

/** アニメーションループの停止 */
export function stopAnimationLoop() {
	if (animFrameId !== null) {
		cancelAnimationFrame(animFrameId);
		animFrameId = null;
	}
}

/** ビューワの破棄 (GPU リソースとリスナの後始末) */
export function disposeVrmViewer() {
	invalidatePendingLoad();
	stopAnimationLoop();
	window.removeEventListener('resize', resizeVrmViewer);
	window.removeEventListener('pointermove', onPointerMove);

	if (currentVrm) {
		if (scene) scene.remove(currentVrm.scene);
		if (VRMUtils?.deepDispose) {
			VRMUtils.deepDispose(currentVrm.scene);
		}
		currentVrm = null;
		headNode = null;
		armSigns = null;
	}

	if (gazeTarget && scene) {
		scene.remove(gazeTarget);
		gazeTarget = null;
	}

	if (renderer) {
		renderer.dispose();
		renderer = null;
	}
	scene = null;
	camera = null;
}
