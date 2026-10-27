/* Subsystem: Avatar Rendering */
console.log("[Deining Live2D] avatar-core-v2.js v=137 loaded");

// ====== Live2D Model3 JSON & Expressions 透過的パッチフック ======
(function () {
    if (window._deiningLive2DFetchHooked) return;
    window._deiningLive2DFetchHooked = true;

    const _origFetch = window.fetch;
    window.fetch = async function (url, options) {
        const urlStr = String(url);

        // [Security] 宛先ホワイトリスト検査 (同一オリジン、または localhost/127.0.0.1 の 5000/5001/8080)
        try {
            const parsedUrl = new URL(urlStr, window.location.href);
            const isBlobOrData = parsedUrl.protocol === 'blob:' || parsedUrl.protocol === 'data:';
            const isSameOrigin = parsedUrl.origin === window.location.origin;
            const isAllowedLocalHost = (parsedUrl.hostname === '127.0.0.1' || parsedUrl.hostname === 'localhost' || parsedUrl.hostname === window.location.hostname) &&
                                       ['5000', '5001', '8080'].includes(parsedUrl.port || (parsedUrl.protocol === 'http:' ? '80' : '443'));

            if (!isBlobOrData && !isSameOrigin && !isAllowedLocalHost) {
                console.warn('[Security] Blocked external fetch to unauthorized destination:', urlStr);
                return new Response(JSON.stringify({ error: 'Blocked by avatar-core security policy' }), {
                    status: 403,
                    statusText: 'Forbidden',
                    headers: { 'Content-Type': 'application/json' }
                });
            }
        } catch (e) {
            console.warn('[Security] Blocked invalid fetch URL:', urlStr, e);
            return new Response(JSON.stringify({ error: 'Invalid URL blocked' }), {
                status: 400,
                statusText: 'Bad Request'
            });
        }

        // 1. ulvm2_0001.model3.json のインターセプト（Expressionsが空なら動的補完）
        if (urlStr.includes('ulvm2_0001.model3.json') || urlStr.includes('銀髪の女の子')) {
            try {
                const response = await _origFetch.apply(this, arguments);
                if (response.ok) {
                    const data = await response.clone().json();
                    if (!data.FileReferences) data.FileReferences = {};
                    if (!data.FileReferences.Expressions || data.FileReferences.Expressions.length === 0) {
                        data.FileReferences.Expressions = [
                            { Name: "idle", File: "animation/idle.exp3.json" },
                            { Name: "angry", File: "animation/angry.exp3.json" },
                            { Name: "blush", File: "animation/blush.exp3.json" },
                            { Name: "cry", File: "animation/cry.exp3.json" },
                            { Name: "smile", File: "animation/smile.exp3.json" },
                            { Name: "surprised", File: "animation/surprised.exp3.json" },
                            { Name: "sleep", File: "animation/sleep.exp3.json" },
                            { Name: "xd", File: "animation/XD.exp3.json" },
                            { Name: "heart", File: "animation/fx_1_heart.exp3.json" },
                            { Name: "sweat", File: "animation/fx_4_sweat.exp3.json" },
                            { Name: "question", File: "animation/fx_7_question.exp3.json" },
                            { Name: "angry_mark", File: "animation/fx_13_angry.exp3.json" }
                        ];
                        console.log("🌸 Live2D: model3.json に 12種類の Expressions 定義を自動注入しました！");
                        return new Response(JSON.stringify(data), {
                            status: response.status,
                            statusText: response.statusText,
                            headers: { 'Content-Type': 'application/json' }
                        });
                    }
                }
                return response;
            } catch (e) {
                return _origFetch.apply(this, arguments);
            }
        }

        // 1-2. fav_usaneko_alwb.model3.json のインターセプト
        // 茶髪モデルは公式定義で表情が揃っているため、存在しない架空ファイルを注入せずそのまま通す

        // 2. 公式フォルダに存在しない animation/idle.exp3.json の仮想提供
        if (urlStr.includes('animation/idle.exp3.json')) {
            try {
                const response = await _origFetch.apply(this, arguments);
                if (response.status === 404 || !response.ok) {
                    const idleExp = {
                        Type: "Live2D Expression",
                        Parameters: []
                    };
                    return new Response(JSON.stringify(idleExp), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                return response;
            } catch (e) {
                const idleExp = {
                    Type: "Live2D Expression",
                    Parameters: [{ Id: "ParamEyeBallX", Value: 0, Blend: "Add" }]
                };
                return new Response(JSON.stringify(idleExp), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' }
                });
            }
        }

        return _origFetch.apply(this, arguments);
    };
})();

// ====== Live2D Initialization ======
let sysAvatarCtx = null;
let activeExpression = 'idle';
let _currentCheek = 0.0; // 頬染め（Blush）のグローバル滑らかフェード値 (0.0〜1.0)
let isSpeaking = false; // リップシンク用フラグ
let expressionAutoResetTimer = null; // 全表情の自動解除用統一タイマー

// 表情自動解除タイマーのクリア
function clearExpressionAutoReset() {
    if (expressionAutoResetTimer) {
        clearTimeout(expressionAutoResetTimer);
        expressionAutoResetTimer = null;
    }
}

// 表情自動解除タイマーのセット（delayMs 後に自動で idle へ復帰）
function scheduleExpressionAutoReset(delayMs = 3500) {
    clearExpressionAutoReset();
    if (delayMs > 0) {
        expressionAutoResetTimer = setTimeout(() => {
            if (sysAvatarCtx && activeExpression !== 'idle') {
                console.log(`🌸 [Deining Live2D] 表情(${activeExpression})の自動解除タイマー作動 ➔ idleに復帰`);
                applyAvatarExpression('idle');
            }
        }, delayMs);
    }
}

// 着せ替え用変数
let activeOutfitId = 0;
function rotateAvatarOutfit() {
    let maxCostume = 5; // 標準モデル（ulvm2_0001）は5種類（デフォルト含め）
    const currentModelUrl = localStorage.getItem('sys_avatar_pref') || '';
    if (currentModelUrl.includes('fav_usaneko_alwb') || currentModelUrl.includes('茶髪の癒し系お姉さん')) {
        maxCostume = 1; // fav_usaneko_alwb は1種類（衣装差分なし）
    }
    activeOutfitId = (activeOutfitId + 1) % maxCostume;
}

// デフォルトのulvm2_0001用表情定義（公式ダウンロード状態でも動くように動的補完）
const DEFAULT_ULVM_EXPRESSIONS = [
    { Name: "idle", File: "animation/idle.exp3.json" },
    { Name: "angry", File: "animation/angry.exp3.json" },
    { Name: "blush", File: "animation/blush.exp3.json" },
    { Name: "cry", File: "animation/cry.exp3.json" },
    { Name: "smile", File: "animation/smile.exp3.json" },
    { Name: "surprised", File: "animation/surprised.exp3.json" },
    { Name: "sleep", File: "animation/sleep.exp3.json" },
    { Name: "xd", File: "animation/XD.exp3.json" },
    { Name: "heart", File: "animation/fx_1_heart.exp3.json" },
    { Name: "sweat", File: "animation/fx_4_sweat.exp3.json" },
    { Name: "question", File: "animation/fx_7_question.exp3.json" },
    { Name: "angry_mark", File: "animation/fx_13_angry.exp3.json" }
];

// 全表情パラメータの安全リセットヘルパー（差分パーツ・エフェクトのみを初期化し、顔の基本構造は絶対に破壊しない）
function resetAllAvatarExpressionParams(coreModel) {
    if (!coreModel || typeof coreModel.setParameterValueById !== 'function') return;
    const safeDiffParams = [
        'ParamMouthForm', 'ParamBrowLAngle', 'ParamBrowRAngle', 'ParamBrowLForm', 'ParamBrowRForm',
        'ParamBrowLY', 'ParamBrowRY', 'Param80', 'Param81', 'Param82', 'Param13',
        'e2_Param517', 'e2_Param519', 'e2_Param520', 'e2_Param521',
        'e2_Param3', 'e2_Param4', 'e2_Param6', 'e2_Param7', 'e2_Param8', 'e2_Param9',
        'e2_Param10', 'e2_Param11', 'e2_Param16', 'e2_Param17',
        'e_Param47', 'e_Param48', 'e_Param52', 'e_Param54', 'e_Param55', 'e_Param56',
        'e_Param57', 'e_Param58', 'e_Param59', 'e_Param60', 'e_Param61', 'e_Param62', 'e_Param110',
        'Param519', 'Param520', 'Param521'
    ];
    for (const p of safeDiffParams) {
        try { coreModel.setParameterValueById(p, 0); } catch (e) { }
    }
    // 基本パーツのON/OFFフラグ（目・口）および目開き・チークの完全復帰（1.0が表示、0.0が非表示）
    try {
        coreModel.setParameterValueById('Param3', 1.0); // 銀髪モデル: 通常口 表示 (Moc3 デフォルト値: 1.0)
        coreModel.setParameterValueById('e2_Param18', 1.0); // 茶髪モデル: 通常口 表示 (Moc3 デフォルト値: 1.0)
        coreModel.setParameterValueById('e2_Param55', 1.0); // 通常目 表示 (Moc3 デフォルト値: 1.0)
        // まばたき・表情フェードとの衝突防止のため、ParamEyeLOpen/ROpenの強制1.0代入は行わず自然復帰させる
        coreModel.setParameterValueById('ParamEyeLSmile', 0);
        coreModel.setParameterValueById('ParamEyeRSmile', 0);
        coreModel.setParameterValueById('e2_ParamCheek', 0);
        coreModel.setParameterValueById('ParamCheek', 0);
    } catch (e) { }
}

let idleCleanupTimeout = null;

// 表情セット用ヘルパー関数
function applyAvatarExpression(exp, autoResetMs) {
    if (!sysAvatarCtx) return;
    activeExpression = exp || 'idle';

    if (idleCleanupTimeout) {
        clearTimeout(idleCleanupTimeout);
        idleCleanupTimeout = null;
    }

    // UI (Live2DControls.svelte など) への通知
    window.dispatchEvent(new CustomEvent('deiningAvatarExpressionChanged', {
        detail: { expression: activeExpression }
    }));

    const core = (sysAvatarCtx.internalModel && sysAvatarCtx.internalModel.coreModel) ? sysAvatarCtx.internalModel.coreModel : null;

    if (activeExpression === 'idle') {
        clearExpressionAutoReset();
        _currentCheek = 0.0;

        // 1. Cubism Native ExpressionManager によるフェードアウト復帰（stopAllMotions等は呼ばず、自然なクロスフェードを維持）
        let idleIdx = -1;
        if (sysAvatarCtx.internalModel && sysAvatarCtx.internalModel.settings && sysAvatarCtx.internalModel.settings.expressions) {
            const exps = sysAvatarCtx.internalModel.settings.expressions;
            idleIdx = exps.findIndex(e => (e.Name || e.name || "").toLowerCase() === 'idle');
        }

        if (idleIdx !== -1) {
            // idle表情ファイルがあるモデル（銀髪等）: ネイティブのフェードアウト＆idle適用
            sysAvatarCtx.expression(idleIdx).catch(() => { });
        } else {
            // idle定義がないモデル（茶髪等）: resetExpression でdefaultExpressionへフェード復帰
            if (sysAvatarCtx.internalModel && sysAvatarCtx.internalModel.motionManager && sysAvatarCtx.internalModel.motionManager.expressionManager) {
                const em = sysAvatarCtx.internalModel.motionManager.expressionManager;
                try {
                    if (typeof em.resetExpression === 'function') {
                        em.resetExpression();
                    }
                } catch (e) { }
            } else {
                try {
                    sysAvatarCtx.expression(null).catch(() => { });
                } catch (e) { }
            }
        }

        // フェードアウト中（約0.6秒）はLive2Dネイティブの滑らかな減衰演出を邪魔せず、
        // フェード完了後に万が一残存した差分パーツのみを安全に最終クリーンアップ
        idleCleanupTimeout = setTimeout(() => {
            if (activeExpression === 'idle' && sysAvatarCtx && sysAvatarCtx.internalModel && sysAvatarCtx.internalModel.coreModel) {
                resetAllAvatarExpressionParams(sysAvatarCtx.internalModel.coreModel);
            }
        }, 650);
        return;
    }

    // 新しい表情を適用する前に、顔パーツの基本表示（口・目）を確実に保証
    if (core) {
        try {
            core.setParameterValueById('Param3', 1.0); // 銀髪 通常口表示
            core.setParameterValueById('e2_Param18', 1.0); // 茶髪 通常口表示
            core.setParameterValueById('e2_Param55', 1.0); // 通常目表示
        } catch (e) { }

        if (activeExpression === 'angry') {
            try {
                core.setParameterValueById('ParamMouthForm', -1.0);
                core.setParameterValueById('Param80', 0);
            } catch (e) { }
        } else if (activeExpression === 'smile' || activeExpression === 'xd') {
            try {
                core.setParameterValueById('ParamMouthForm', 1.0);
                core.setParameterValueById('Param80', 1.0);
            } catch (e) { }
        }
    }

    let targetIndex = -1;
    // 異なるモデル間で表情名の差異を吸収するための検索処理
    if (sysAvatarCtx.internalModel && sysAvatarCtx.internalModel.settings && sysAvatarCtx.internalModel.settings.expressions) {
        const exps = sysAvatarCtx.internalModel.settings.expressions;
        const getSearchText = (e) => ((e.Name || e.name || "") + " " + (e.File || e.file || "")).toLowerCase();

        // 1. 完全一致
        targetIndex = exps.findIndex(e => (e.Name || e.name) === activeExpression);

        // 2. 部分一致・エイリアス一致
        if (targetIndex === -1) {
            if (activeExpression === 'angry') {
                targetIndex = exps.findIndex(e => getSearchText(e).includes('02.angry') || getSearchText(e).includes('angry.exp3') || getSearchText(e).includes('angry2'));
            } else if (activeExpression === 'smile') {
                targetIndex = exps.findIndex(e => getSearchText(e).includes('08.xd') || getSearchText(e).includes('smile.exp3') || getSearchText(e).includes('smile') || getSearchText(e).includes('12.cat_mouth') || getSearchText(e).includes('xd'));
            } else if (activeExpression === 'cry' || activeExpression === 'sad') {
                targetIndex = exps.findIndex(e => getSearchText(e).includes('03.cry') || getSearchText(e).includes('cry.exp3') || getSearchText(e).includes('cry') || getSearchText(e).includes('pale'));
            } else if (activeExpression === 'surprised') {
                targetIndex = exps.findIndex(e => getSearchText(e).includes('13.surprised') || getSearchText(e).includes('surprised.exp3') || getSearchText(e).includes('surprised') || getSearchText(e).includes('bikkuri'));
            } else if (activeExpression === 'blush') {
                targetIndex = exps.findIndex(e => getSearchText(e).includes('01.blush') || getSearchText(e).includes('blush.exp3') || getSearchText(e).includes('blush') || getSearchText(e).includes('tereru') || getSearchText(e).includes('tere'));
            }

            if (targetIndex === -1) {
                targetIndex = exps.findIndex(e => getSearchText(e).includes(activeExpression.toLowerCase()));
            }
        }
    }

    if (targetIndex !== -1) {
        sysAvatarCtx.expression(targetIndex).catch(() => { });
    } else {
        sysAvatarCtx.expression(activeExpression).catch(() => { });
    }

    // 自動解除タイマーをセット（驚きは1.5秒、その他は3.5秒、明示指定があれば優先）
    const defaultDelay = (activeExpression === 'surprised') ? 1500 : 3500;
    const delay = (typeof autoResetMs === 'number') ? autoResetMs : defaultDelay;
    if (delay > 0) {
        scheduleExpressionAutoReset(delay);
    }
}

// リアルタイム音声解析（リップシンク）用
let audioContext = null;
let analyser = null;
let _lastSampledMouthOpen = 0;
function getMouthOpenValue() {
    if (!isSpeaking || !analyser || !dataArray) {
        _lastSampledMouthOpen = 0;
        return 0;
    }
    analyser.getByteFrequencyData(dataArray);
    let sum = 0;
    for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
    const avg = sum / dataArray.length;
    const threshold = 18.0;
    const currentVolume = avg < threshold ? 0 : Math.min(0.85, (avg - threshold) / 90.0);
    _lastSampledMouthOpen = _lastSampledMouthOpen * 0.35 + currentVolume * 0.65;
    return _lastSampledMouthOpen;
}
let dataArray = null;
let currentVolume = 0;
let isAudioUnlocked = false;

// モバイルブラウザ向け AudioContext アンロック（初回ユーザー操作時に無音を再生して自動再生制限を解除）
function unlockAudio() {
    if (isAudioUnlocked) return;
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!audioContext && AudioCtx) {
            audioContext = new AudioCtx();
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 256;
            dataArray = new Uint8Array(analyser.frequencyBinCount);
        }
        if (audioContext && audioContext.state === 'suspended') {
            audioContext.resume();
        }
        if (audioContext) {
            const buffer = audioContext.createBuffer(1, 1, 22050);
            const source = audioContext.createBufferSource();
            source.buffer = buffer;
            source.connect(audioContext.destination);
            source.start(0);
            isAudioUnlocked = true;
            console.log("[Audio] AudioContext unlocked for mobile browser");
        }
    } catch (e) {
        console.warn("[Audio] Failed to unlock audio:", e);
    }
}
if (typeof window !== 'undefined') {
    window.addEventListener('click', unlockAudio, { passive: true });
}

// 接続環境に応じたTTSエンドポイントの取得（ゲートウェイ経由時は同一オリジン相対パス中継を使用）
function getTtsEndpoint() {
    if (typeof window !== 'undefined' && window.location && window.location.port !== '8080') {
        return '/__deinei/tts/tts';
    }
    const host = (window.location && window.location.hostname) || '127.0.0.1';
    return `http://${host}:5001/tts`;
}

// --- テキスト解析による感情・表情のタイムライン生成 ---
function analyzeExpressionTimeline(text) {
    if (text) {
        let detectedEmotion = null;
        const emotionRules = [
            { exp: 'heart', keywords: ['えっち', '変態', 'エッチ', '///', 'ドキドキ', '気持ち良い', '好き', '♡', '❤️', '💕', '💖', '💗', '💓', '💘'] },
            { exp: 'blush', keywords: ['照れ', '恥ずかしい', 'モジモジ', 'はずかしい', '♡', '😳', '🥰', '😚', '😳💕'] },
            { exp: 'xd', keywords: ['笑顔', 'あはは', 'たのしい', '楽しい', 'やったー', 'にこにこ', 'わーい', 'わあ', '😆', '🤣', '✨', '✨️'] },
            { exp: 'smile', keywords: ['ふふ', 'えへへ', 'なるほど', 'うんうん', '微笑', '笑', '😊', '☺️', '🙂', '😋', '😄', '😁'] },
            { exp: 'angry', keywords: ['ぷんぷん', 'バカ', 'ダメ', 'うるさい', '怒るよ', 'むー', '怒', '💢', '😡', '😠', '😤'] },
            { exp: 'cry', keywords: ['悲', '泣', 'つらい', '寂しい', 'ごめん', 'ごめんね', '怖い', 'えーん', '痛', '😢', '😭', '🥺', '😿'] },
            { exp: 'surprised', keywords: ['驚', 'えっ', '本当に', '！？', 'びっくり', 'まさか', '😦', '💦', '😲', '😮', '😱', '🙀', '❗', '⁉️'] },
            { exp: 'sleep', keywords: ['眠い', 'ふぁあ', 'おやすみ', 'ねむい', 'ウトウト', '😴', '💤', '🥱'] },
            { exp: 'question', keywords: ['はてな', '疑問', 'なぜ', 'どうして', 'かしら', 'のかな？', '🤔', '❓', '❔'] }
        ];
        for (const rule of emotionRules) {
            if (rule.keywords.some(kw => text.includes(kw))) {
                detectedEmotion = rule.exp;
                break;
            }
        }
        try {
            window.dispatchEvent(new CustomEvent('deiningAvatarEmotionDetected', {
                detail: { emotion: detectedEmotion, text: text }
            }));
        } catch (e) { }
    }
    if (!sysAvatarCtx) return [];

    const rules = [
        { exp: 'heart', keywords: ['えっち', '変態', 'エッチ', '///', 'ドキドキ', '気持ち良い', '好き', '♡', '❤️', '💕', '💖', '💗', '💓', '💘'] },
        { exp: 'blush', keywords: ['照れ', '恥ずかしい', 'モジモジ', 'はずかしい', '♡', '😳', '🥰', '😚', '😳💕'] },
        { exp: 'xd', keywords: ['笑顔', 'あはは', 'たのしい', '楽しい', 'やったー', 'にこにこ', 'わーい', 'わあ', '😆', '🤣', '✨', '✨️'] },
        { exp: 'smile', keywords: ['ふふ', 'えへへ', 'なるほど', 'うんうん', '微笑', '笑', '😊', '☺️', '🙂', '😋', '😄', '😁'] },
        { exp: 'angry', keywords: ['ぷんぷん', 'バカ', 'ダメ', 'うるさい', '怒るよ', 'むー', '怒', '💢', '😡', '😠', '😤'] },
        { exp: 'cry', keywords: ['悲', '泣', 'つらい', '寂しい', 'ごめん', 'ごめんね', '怖い', 'えーん', '痛', '😢', '😭', '🥺', '😿'] },
        { exp: 'surprised', keywords: ['驚', 'えっ', '本当に', '！？', 'びっくり', 'まさか', '😦', '💦', '😲', '😮', '😱', '🙀', '❗', '⁉️'] },
        { exp: 'sleep', keywords: ['眠い', 'ふぁあ', 'おやすみ', 'ねむい', 'ウトウト', '😴', '💤', '🥱'] },
        { exp: 'question', keywords: ['はてな', '疑問', 'なぜ', 'どうして', 'かしら', 'のかな？', '🤔', '❓', '❔'] }
    ];

    let events = [];
    const textLen = text.length;
    if (textLen === 0) return events;

    for (const rule of rules) {
        for (const kw of rule.keywords) {
            if (!kw) continue;
            let idx = 0;
            while ((idx = text.indexOf(kw, idx)) !== -1) {
                events.push({ ratio: idx / textLen, exp: rule.exp, kw: kw });
                idx += kw.length;
            }
        }
    }

    // 割合順（登場順）にソート
    events.sort((a, b) => a.ratio - b.ratio);

    // 最初（0%）の表情を決定
    let initialExp = 'idle';
    if (events.length > 0 && events[0].ratio < 0.1) {
        initialExp = events[0].exp;
    }

    // 0%地点のデフォルトイベントを追加
    if (events.length === 0 || events[0].ratio >= 0.1) {
        events.unshift({ ratio: 0, exp: initialExp, kw: 'default' });
    }

    return events;
}

// --- TTSキュー & 先読みプリフェッチキャッシュ管理 ---
const ttsQueue = [];
const ttsPrefetchCache = new Map(); // cleanText -> { promise, audioUrl }
let prefetchAbortController = null;
let isProcessingTts = false;
let currentAudioPlayer = null;
let ttsGenerationId = 0;
let ttsAbortController = null;

// 先読みプリフェッチAPI (バックグラウンドで合成完了させておき、再生時に待ち時間0秒を実現)
window.prefetchTTS = function (text) {
    const isTtsEnabled = localStorage.getItem('deining_tts_enabled') === 'true';
    if (!isTtsEnabled) return Promise.resolve(null);

    const cleanText = sanitizeTextForTTS(text);
    if (!cleanText) return Promise.resolve(null);

    if (ttsPrefetchCache.has(cleanText)) {
        return ttsPrefetchCache.get(cleanText).promise;
    }

    if (!prefetchAbortController) {
        prefetchAbortController = new AbortController();
    }

    const ttsHost = window.location.hostname || '127.0.0.1';
    let currentSpeed = 1.2;
    try {
        const savedSpeed = localStorage.getItem('deining_tts_speed');
        if (savedSpeed !== null) currentSpeed = parseFloat(savedSpeed);
    } catch (e) { }

    const entry = {
        audioUrl: null,
        promise: null
    };

    entry.promise = (async () => {
        try {
            console.log(`⚡ [先読みプリフェッチ開始] "${cleanText.slice(0, 18)}..."`);
            const response = await fetch(getTtsEndpoint(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: cleanText, speed: currentSpeed, split_chunks: true }),
                mode: 'cors',
                signal: prefetchAbortController ? prefetchAbortController.signal : undefined
            });
            if (response.ok) {
                const blob = await response.blob();
                const audioUrl = URL.createObjectURL(blob);
                entry.audioUrl = audioUrl;
                console.log(`✅ [先読み完了・キャッシュ保持] "${cleanText.slice(0, 18)}..." 準備完了 (即時再生可能) [キャッシュ件数: ${ttsPrefetchCache.size}]`);
                return audioUrl;
            }
            return null;
        } catch (e) {
            if (e.name !== 'AbortError') {
                console.warn(`[先読み例外] "${cleanText.slice(0, 18)}...":`, e);
            }
            return null;
        }
    })();

    ttsPrefetchCache.set(cleanText, entry);
    return entry.promise;
};

function dispatchTtsState() {
    if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('deiningTtsStateChanged', {
            detail: { isPlaying: isProcessingTts || ttsQueue.length > 0 || currentAudioPlayer !== null }
        }));
    }
}

async function processTtsQueue() {
    if (isProcessingTts || ttsQueue.length === 0) {
        dispatchTtsState();
        return;
    }
    isProcessingTts = true;
    dispatchTtsState();

    const currentGen = ++ttsGenerationId;
    if (ttsAbortController) {
        try { ttsAbortController.abort(); } catch (e) { }
    }
    ttsAbortController = new AbortController();

    const queueItem = ttsQueue.shift();
    const text = typeof queueItem === 'string' ? queueItem : queueItem.text;
    const emotionText = typeof queueItem === 'string' ? queueItem : (queueItem.emotionText || queueItem.text);
    try {
        const timeline = analyzeExpressionTimeline(emotionText);
        let currentEventIndex = 0;

        let audioUrl = null;
        // ⚡ 先読みキャッシュチェック：既に合成完了していれば待ち時間0秒で即再生！
        if (ttsPrefetchCache.has(text)) {
            const cached = ttsPrefetchCache.get(text);
            ttsPrefetchCache.delete(text);
            console.log(`⏳ [先読みキャッシュ参照] "${text.slice(0, 18)}..." (待機中/取得中)`);
            audioUrl = cached.audioUrl || (await cached.promise);
            if (currentGen !== ttsGenerationId) {
                console.log('[TTS] Prefetch audio cancelled by stopTTS or superseded');
                return;
            }
            if (audioUrl) {
                console.log(`🚀 [先読みヒット！待ち時間0秒で即時再生] "${text.slice(0, 18)}..."`);
            } else {
                console.warn(`⚠️ [先読みキャッシュ取得失敗 (null)] "${text.slice(0, 18)}..." ➔ 通常合成リクエストへ移行`);
            }
        } else {
            console.log(`ℹ️ [先読みキャッシュ未登録] "${text.slice(0, 18)}..." ➔ 通常合成リクエスト開始`);
        }

        if (!audioUrl) {
            const ttsHost = window.location.hostname || '127.0.0.1';
            let currentSpeed = 1.2;
            let currentSplitChunks = true;
            try {
                const savedSpeed = localStorage.getItem('deining_tts_speed');
                if (savedSpeed !== null) {
                    currentSpeed = parseFloat(savedSpeed);
                } else {
                    currentSpeed = window.deiningTtsSpeed ||
                        (window.parent && window.parent.deiningTtsSpeed) ||
                        (window.top && window.top.deiningTtsSpeed) ||
                        1.0;
                }
                const savedSplit = localStorage.getItem('deining_tts_split_chunks');
                if (savedSplit !== null) {
                    currentSplitChunks = savedSplit === 'true';
                }
            } catch (e) {
                currentSpeed = window.deiningTtsSpeed || 1.2;
            }
            console.log(`🔊 TTSリクエスト送信中 (${getTtsEndpoint()}): speed=${currentSpeed}, split_chunks=${currentSplitChunks}`);
            const response = await fetch(getTtsEndpoint(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: text, speed: currentSpeed, split_chunks: currentSplitChunks }),
                mode: 'cors',
                signal: ttsAbortController.signal
            });

            if (currentGen !== ttsGenerationId) {
                console.log('[TTS] Request cancelled by stopTTS or superseded');
                return;
            }

            if (response.ok) {
                const blob = await response.blob();
                if (currentGen !== ttsGenerationId) return;
                audioUrl = URL.createObjectURL(blob);
            }
        }

        if (currentGen !== ttsGenerationId) {
            if (audioUrl && audioUrl.startsWith('blob:')) {
                try { URL.revokeObjectURL(audioUrl); } catch (e) { }
            }
            return;
        }

        if (audioUrl) {
            currentAudioPlayer = new Audio(audioUrl);
            currentAudioPlayer.crossOrigin = "anonymous";
            const audio = currentAudioPlayer;

            if (!audioContext) {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                audioContext = new AudioContext();
                analyser = audioContext.createAnalyser();
                analyser.fftSize = 256;
                dataArray = new Uint8Array(analyser.frequencyBinCount);
            }
            if (audioContext.state === 'suspended') {
                await audioContext.resume();
            }

            const source = audioContext.createMediaElementSource(audio);
            source.connect(analyser);
            analyser.connect(audioContext.destination);

            audio.addEventListener('play', () => {
                if (currentGen !== ttsGenerationId) return;
                isSpeaking = true;
                dispatchTtsState();
            });

            audio.addEventListener('ended', () => {
                if (currentGen !== ttsGenerationId) return;
                isSpeaking = false;
                currentAudioPlayer = null;

                // 音声終了時は3秒待ってから自然に通常（idle）に戻す
                scheduleExpressionAutoReset(3000);

                // 次の音声を0.2秒待ってから再生（自然なブレス/間）
                isProcessingTts = false;
                dispatchTtsState();
                setTimeout(() => {
                    if (currentGen === ttsGenerationId) {
                        processTtsQueue();
                    }
                }, 200);
            });

            audio.addEventListener('pause', () => {
                if (currentGen !== ttsGenerationId) return;
                isSpeaking = false;
                dispatchTtsState();
            });

            audio.addEventListener('timeupdate', () => {
                if (currentGen !== ttsGenerationId) return;
                if (audio.duration && sysAvatarCtx) {
                    const ratio = audio.currentTime / audio.duration;
                    while (currentEventIndex < timeline.length && ratio >= timeline[currentEventIndex].ratio) {
                        const ev = timeline[currentEventIndex];
                        const resetDelay = (ev.exp === 'surprised') ? 1500 : 3500;
                        applyAvatarExpression(ev.exp, resetDelay);
                        currentEventIndex++;
                    }
                }
            });

            if (currentGen !== ttsGenerationId) {
                try { audio.pause(); } catch (e) { }
                return;
            }
            audio.play();
        } else {
            if (currentGen !== ttsGenerationId) return;
            currentAudioPlayer = null;
            isProcessingTts = false;
            processTtsQueue();
        }
    } catch (e) {
        if (currentGen !== ttsGenerationId) {
            // キャンセルされた古い世代のリクエストなら、最新リクエストの状態を破壊しないよう何もしないで終了
            return;
        }
        console.error('TTSエラー:', e);
        currentAudioPlayer = null;
        isProcessingTts = false;
        processTtsQueue();
    }
}

// --- TTS再生およびリップシンク・表情タイムライン制御 ---
function sanitizeTextForTTS(rawText) {
    if (!rawText) return '';
    let t = rawText;
    // 1. <think>タグの除去
    t = t.replace(/<think>[\s\S]*?<\/think>/gi, '');
    // 2. コードブロックの除去
    t = t.replace(/```[\s\S]*?```/g, '');
    t = t.replace(/`([^`]+)`/g, '$1');
    // 3. Markdown見出し記号
    t = t.replace(/^#{1,6}\s+/gm, '');
    // 4. Markdown箇条書き・番号付きリスト記号
    t = t.replace(/^[\s]*[-*+]\s+/gm, '');
    t = t.replace(/^[\s]*\d+\.\s+/gm, '');
    // 5. Markdown太字・斜体の展開（**文字** -> 文字 をト書き判定前に実行）
    t = t.replace(/\*\*\*([^*]+)\*\*\*/g, '$1');
    t = t.replace(/\*\*([^*]+)\*\*/g, '$1');
    t = t.replace(/___([^_]+)___/g, '$1');
    t = t.replace(/__([^_]+)__/g, '$1');
    // 6. Markdownリンク
    t = t.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
    // 7. ト書き（アスタリスクで囲まれた動作描写）の除去
    t = t.replace(/＊+([^＊]+)＊+/g, '');
    t = t.replace(/\*+([^*]+)\*+/g, '');
    // 8. 動作描写・補足・感情表現を表す丸カッコ内のテキストを無条件に除外（TTS音読完全防止）
    t = t.replace(/（[\s\S]*?）/g, '');
    t = t.replace(/\([\s\S]*?\)/g, '');
    // 9. 不要記号の整理
    t = t.replace(/^[>|]\s*/gm, '');
    t = t.replace(/[*＊]/g, '');
    // 10. 絵文字・装飾記号・異体字セレクタ等の除去 (TTSへの絵文字送信防止)
    t = t.replace(/[\p{Extended_Pictographic}\p{Emoji_Presentation}\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B50}\u{2B55}\u{203C}\u{2049}\u{FE00}-\u{FE0F}\u{200D}\u{20E3}\u{E0020}-\u{E007F}]+/gu, '');
    t = t.replace(/「\s*」|『\s*』|“\s*”|"\s*"/g, '');
    return t.trim();
}

window.playTTS = function (text, force = false, rawTextForEmotion = '') {
    // UI側でTTSが有効化されているかチェック（forceがtrueなら無視）
    if (!force) {
        const isTtsEnabled = localStorage.getItem('deining_tts_enabled') === 'true';
        if (!isTtsEnabled) return;
    }

    const cleanText = sanitizeTextForTTS(text);
    if (!cleanText) return;

    ttsQueue.push({ text: cleanText, emotionText: rawTextForEmotion || text });
    processTtsQueue();
};

// 時分割パイプライン用: 音声合成が完了してブラウザで再生が始まった瞬間(play発火)にresolveするPromise
window.synthesizeAndPlayTTS = async function (text, force = false, rawTextForEmotion = '') {
    if (!force) {
        const isTtsEnabled = localStorage.getItem('deining_tts_enabled') === 'true';
        if (!isTtsEnabled) return { success: false, reason: 'disabled' };
    }

    const cleanText = sanitizeTextForTTS(text);
    if (!cleanText) return { success: true, empty: true };
    const emotionText = rawTextForEmotion || text;

    const currentGen = ++ttsGenerationId;
    if (ttsAbortController) {
        try { ttsAbortController.abort(); } catch (e) { }
    }
    ttsAbortController = new AbortController();

    isProcessingTts = true;
    dispatchTtsState();

    return new Promise(async (resolve) => {
        let isResolved = false;
        const safeResolve = (res) => {
            if (!isResolved) {
                isResolved = true;
                resolve(res);
            }
        };

        // 5秒タイムアウト保護
        const timeoutId = setTimeout(() => {
            console.warn('[synthesizeAndPlayTTS] Timeout (5000ms), resuming LLM stream');
            safeResolve({ success: false, timeout: true });
        }, 5000);

        try {
            const timeline = analyzeExpressionTimeline(emotionText);
            let currentEventIndex = 0;

            let audioUrl = null;
            if (ttsPrefetchCache.has(cleanText)) {
                const cached = ttsPrefetchCache.get(cleanText);
                ttsPrefetchCache.delete(cleanText);
                audioUrl = cached.audioUrl || (await cached.promise);
                if (audioUrl) {
                    console.log(`🚀 [synthesizeAndPlayTTS 先読みヒット！即時再生] "${cleanText.slice(0, 18)}..."`);
                }
            }

            if (!audioUrl) {
                const ttsHost = window.location.hostname || '127.0.0.1';
                let currentSpeed = 1.2;
                let currentSplitChunks = true;
                try {
                    const savedSpeed = localStorage.getItem('deining_tts_speed');
                    if (savedSpeed !== null) currentSpeed = parseFloat(savedSpeed);
                } catch (e) { }

                console.log(`🔊 [パイプライン] TTS合成リクエスト (${getTtsEndpoint()}): "${cleanText.slice(0, 20)}..."`);
                const response = await fetch(getTtsEndpoint(), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: cleanText, speed: currentSpeed, split_chunks: currentSplitChunks }),
                    mode: 'cors',
                    signal: ttsAbortController.signal
                });

                if (currentGen !== ttsGenerationId) {
                    clearTimeout(timeoutId);
                    safeResolve({ success: false, aborted: true });
                    return;
                }

                if (response.ok) {
                    const blob = await response.blob();
                    if (currentGen !== ttsGenerationId) {
                        clearTimeout(timeoutId);
                        safeResolve({ success: false, aborted: true });
                        return;
                    }

                    audioUrl = URL.createObjectURL(blob);
                }
            }

            if (audioUrl) {
                if (currentAudioPlayer) {
                    try { currentAudioPlayer.pause(); } catch (e) { }
                }
                currentAudioPlayer = new Audio(audioUrl);
                currentAudioPlayer.crossOrigin = "anonymous";
                const audio = currentAudioPlayer;

                if (!audioContext) {
                    const AudioContext = window.AudioContext || window.webkitAudioContext;
                    audioContext = new AudioContext();
                    analyser = audioContext.createAnalyser();
                    analyser.fftSize = 256;
                    dataArray = new Uint8Array(analyser.frequencyBinCount);
                }
                if (audioContext.state === 'suspended') {
                    await audioContext.resume();
                }

                const source = audioContext.createMediaElementSource(audio);
                source.connect(analyser);
                analyser.connect(audioContext.destination);

                audio.addEventListener('play', () => {
                    isSpeaking = true;
                    clearTimeout(timeoutId);
                    dispatchTtsState();
                    console.log('▶️ [パイプライン] 音声再生開始 ➔ LLMストリーム再開 (先読み開始)');
                    safeResolve({ success: true, started: true });
                });

                audio.addEventListener('ended', () => {
                    isSpeaking = false;
                    currentAudioPlayer = null;

                    // 音声終了時は3秒待ってから自然に通常（idle）に戻す
                    scheduleExpressionAutoReset(3000);

                    isProcessingTts = false;
                    dispatchTtsState();
                    window.dispatchEvent(new CustomEvent('deiningTtsSegmentEnded', { detail: { text: cleanText } }));
                });

                audio.addEventListener('pause', () => {
                    isSpeaking = false;
                    dispatchTtsState();
                });

                audio.addEventListener('timeupdate', () => {
                    if (audio.duration && sysAvatarCtx) {
                        const ratio = audio.currentTime / audio.duration;
                        while (currentEventIndex < timeline.length && ratio >= timeline[currentEventIndex].ratio) {
                            const ev = timeline[currentEventIndex];
                            const resetDelay = (ev.exp === 'surprised') ? 1500 : 3500;
                            applyAvatarExpression(ev.exp, resetDelay);
                            currentEventIndex++;
                        }
                    }
                });

                audio.play().catch((e) => {
                    console.warn('Audio play failed:', e);
                    clearTimeout(timeoutId);
                    safeResolve({ success: false, error: e });
                });
            } else {
                clearTimeout(timeoutId);
                currentAudioPlayer = null;
                isProcessingTts = false;
                dispatchTtsState();
                safeResolve({ success: false, error: 'response_not_ok' });
            }
        } catch (e) {
            clearTimeout(timeoutId);
            if (currentGen === ttsGenerationId) {
                console.error('[synthesizeAndPlayTTS] Error:', e);
            }
            currentAudioPlayer = null;
            isProcessingTts = false;
            dispatchTtsState();
            safeResolve({ success: false, error: e });
        }
    });
};

window.stopTTS = function (clearPrefetch = false) {
    console.log(`[stopTTS] Stopping current TTS audio and clearing queue (clearPrefetch=${clearPrefetch})`);
    ttsGenerationId++;
    if (ttsAbortController) {
        try { ttsAbortController.abort(); } catch (e) { }
        ttsAbortController = null;
    }

    // clearPrefetch が true の場合のみ、先読みキャッシュを完全破棄し、先読みfetchを中断する
    // 「次へ」ボタン等で現在のセリフのみ停止する場合は false となり、先読みキャッシュは次セリフ即時再生のために保持される
    if (clearPrefetch) {
        if (prefetchAbortController) {
            try { prefetchAbortController.abort(); } catch (e) { }
            prefetchAbortController = null;
        }
        for (const [key, entry] of ttsPrefetchCache.entries()) {
            if (entry.audioUrl) {
                try { URL.revokeObjectURL(entry.audioUrl); } catch (e) { }
            } else if (entry.promise) {
                entry.promise.then(url => { if (url) URL.revokeObjectURL(url); }).catch(() => {});
            }
        }
        ttsPrefetchCache.clear();
        console.log('[stopTTS] All prefetch cache cleared');
    }

    ttsQueue.length = 0;
    if (currentAudioPlayer) {
        try {
            currentAudioPlayer.pause();
            currentAudioPlayer.currentTime = 0;
            currentAudioPlayer.src = '';
        } catch (e) { }
        currentAudioPlayer = null;
    }
    isSpeaking = false;
    isProcessingTts = false;
    scheduleExpressionAutoReset(200);
    dispatchTtsState();
    window.dispatchEvent(new CustomEvent('deiningTtsSegmentEnded', { detail: { stopped: true } }));
};

window.addEventListener('deiningGenerationAborted', function () {
    console.log('[avatar-core] Received deiningGenerationAborted event, stopping TTS immediately');
    window.stopTTS(true);
});

// --- ライブ2Dモデルへの拡張機能適用関数 ---
function injectAvatarMods(targetModel) {
    if (!targetModel) return;
    if (targetModel._avatarModsInjected) return;
    targetModel._avatarModsInjected = true;

    let blinkState = 1.0;
    let nextBlinkTime = performance.now() + 3000;
    let isBlinking = false;
    let blinkStartTime = 0;
    let baseEyeL = 1.0;
    let baseEyeR = 1.0;

    function applyParams(coreModel, dt) {
        if (!coreModel) return;

        // 1. 衣装の適用とデフォルト(idle)時のパラメータ保証
        const currentModelUrl = localStorage.getItem('sys_avatar_pref') || '';
        if (currentModelUrl.includes('ulvm2') || currentModelUrl.includes('銀髪の女の子')) {
            coreModel.setParameterValueById('v0000_Param8', activeOutfitId === 0 ? 1 : 0); // デフォルト私服
            coreModel.setParameterValueById('v0052_Param3', activeOutfitId === 1 ? 1 : 0); // 私服2
            coreModel.setParameterValueById('V0101_Param3', activeOutfitId === 2 ? 1 : -1); // ナース (OFF時は0ではなく-1が必須)
            coreModel.setParameterValueById('V0100Param44', activeOutfitId === 3 ? 1 : 0); // 別衣装
            coreModel.setParameterValueById('Param28', activeOutfitId === 4 ? 1 : 0); // スク水
            if (activeExpression === 'idle' || !activeExpression) {
                try {
                    coreModel.setParameterValueById('Param3', 1.0); // 銀髪モデル 通常口 表示 (Moc3 デフォルト値: 1.0)
                    if (!isBlinking) {
                        const curL = coreModel.getParameterValueById('ParamEyeLOpen');
                        const curR = coreModel.getParameterValueById('ParamEyeROpen');
                        if (curL == null || curL <= 0.05) coreModel.setParameterValueById('ParamEyeLOpen', 1.0);
                        if (curR == null || curR <= 0.05) coreModel.setParameterValueById('ParamEyeROpen', 1.0);
                    }
                } catch (e) { }
            }
        } else if (currentModelUrl.includes('fav_usaneko') || currentModelUrl.includes('茶髪の癒し系お姉さん')) {
            coreModel.setParameterValueById('faval_Param10', activeOutfitId);
            if (activeExpression === 'idle' || !activeExpression) {
                try {
                    coreModel.setParameterValueById('e2_Param18', 1.0); // 茶髪モデル 通常口 表示 (Moc3 デフォルト値: 1.0)
                    coreModel.setParameterValueById('e2_Param55', 1.0); // 茶髪モデル 通常目 表示 (Moc3 デフォルト値: 1.0)
                    if (!isBlinking) {
                        const curL = coreModel.getParameterValueById('ParamEyeLOpen');
                        const curR = coreModel.getParameterValueById('ParamEyeROpen');
                        if (curL == null || curL <= 0.05) coreModel.setParameterValueById('ParamEyeLOpen', 1.0);
                        if (curR == null || curR <= 0.05) coreModel.setParameterValueById('ParamEyeROpen', 1.0);
                    }
                } catch (e) { }
            }
        }

        // 2. 頬染めの滑らかなフェードイン・フェードアウト
        // モデルがblush.exp3.jsonを保持している場合はCubismネイティブExpressionManagerのフェード(FadeInTime/FadeOutTime)に任せ、
        // ネイティブ表情が存在しないモデルの場合のみスクリプト側で滑らかにフェード補間を適用
        const hasNativeBlushExp = sysAvatarCtx && sysAvatarCtx.internalModel && sysAvatarCtx.internalModel.settings && sysAvatarCtx.internalModel.settings.expressions && sysAvatarCtx.internalModel.settings.expressions.some(e => ((e.Name || e.name || '') + ' ' + (e.File || e.file || '')).toLowerCase().includes('blush'));
        if (!hasNativeBlushExp) {
            const isBlushing = (activeExpression === 'heart' || activeExpression === 'blush');
            const targetCheek = isBlushing ? 1.0 : 0.0;
            const normalizedDt = (typeof dt === 'number' && dt > 0) ? Math.min(dt, 3.0) : 1.0;
            const cheekSpeed = 0.04 * normalizedDt; // 約500msで自然にふわっと変化
            _currentCheek += (targetCheek - _currentCheek) * Math.min(1.0, cheekSpeed);
            if (Math.abs(targetCheek - _currentCheek) < 0.005) {
                _currentCheek = targetCheek;
            }
            coreModel.setParameterValueById('e2_ParamCheek', _currentCheek);
            coreModel.setParameterValueById('ParamCheek', _currentCheek);
        }

        // 3. リップシンク
        if (isSpeaking && analyser) {
            analyser.getByteFrequencyData(dataArray);
            let sum = 0;
            for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
            const avg = sum / dataArray.length;
            const threshold = 18.0;
            currentVolume = avg < threshold ? 0 : Math.min(0.85, (avg - threshold) / 90.0);
            const prevMouth = coreModel.getParameterValueById('ParamMouthOpenY') || 0;
            const smoothedVolume = prevMouth * 0.35 + currentVolume * 0.65;
            coreModel.setParameterValueById('ParamMouthOpenY', smoothedVolume);
        } else {
            coreModel.setParameterValueById('ParamMouthOpenY', 0);
        }

        // 4. まばたき（平常顔 idle の時のみ実行し、表情発動中は瞬きを抑止）
        const isIdleExpression = (activeExpression === 'idle' || !activeExpression);
        const now = performance.now();
        if (!isIdleExpression) {
            isBlinking = false;
            nextBlinkTime = now + 1500 + Math.random() * 3000;
        } else {
            if (!isBlinking && now > nextBlinkTime) {
                isBlinking = true;
                blinkStartTime = now;
                baseEyeL = coreModel.getParameterValueById('ParamEyeLOpen');
                baseEyeR = coreModel.getParameterValueById('ParamEyeROpen');
                if (baseEyeL == null || baseEyeL <= 0.1) baseEyeL = 1.0;
                if (baseEyeR == null || baseEyeR <= 0.1) baseEyeR = 1.0;
            }
            if (isBlinking) {
                const elapsed = now - blinkStartTime;
                const blinkDuration = 120;
                if (elapsed < blinkDuration) {
                    blinkState = 1.0 - (elapsed / blinkDuration);
                } else if (elapsed < blinkDuration * 2) {
                    blinkState = (elapsed - blinkDuration) / blinkDuration;
                } else {
                    blinkState = 1.0;
                    isBlinking = false;
                    nextBlinkTime = now + 2000 + Math.random() * 4000;
                }
                coreModel.setParameterValueById('ParamEyeLOpen', baseEyeL * blinkState);
                coreModel.setParameterValueById('ParamEyeROpen', baseEyeR * blinkState);
            }
        }
    }

    // A. PixiLive2DDisplay公式の描画直前イベントフック (coreModel.update()の直前に毎フレーム確実に発火)
    const internalModel = targetModel.internalModel;
    if (internalModel && typeof internalModel.on === 'function') {
        internalModel.on('beforeModelUpdate', () => {
            if (internalModel.coreModel) {
                applyParams(internalModel.coreModel, 1.0);
            }
        });
    } else if (targetModel.update) {
        // B. update メソッドのオーバーライド (beforeModelUpdate非対応環境向けフォールバック)
        const originalUpdate = targetModel.update;
        targetModel.update = function (deltaTime) {
            originalUpdate.call(this, deltaTime);
            if (this.internalModel && this.internalModel.coreModel) {
                applyParams(this.internalModel.coreModel, deltaTime);
                if (typeof this.internalModel.coreModel.update === 'function') {
                    try { this.internalModel.coreModel.update(); } catch (e) { }
                }
            }
        };
    }
}

async function safeLoadLive2DModel(modelUrl, app) {
    try {
        const fullUrl = modelUrl + '?v=' + Date.now();
        const model = await PIXI.live2d.Live2DModel.from(fullUrl);

        // Expressionsの動的補完 (未定義または空配列の場合、DEFAULT_ULVM_EXPRESSIONSを注入)
        if (model.internalModel && model.internalModel.settings) {
            const settings = model.internalModel.settings;
            if (!settings.expressions || settings.expressions.length === 0) {
                settings.expressions = JSON.parse(JSON.stringify(DEFAULT_ULVM_EXPRESSIONS));
                console.log('✅ Live2D: Expressions を自動パッチ注入しました。');
            }
        }

        // 未配置ガイダンスがあれば非表示
        const guideEl = document.getElementById('deining-live2d-guidance');
        if (guideEl) guideEl.style.display = 'none';

        return model;
    } catch (err) {
        console.warn('⚠️ Live2Dモデルが読み込めませんでした (未配置またはパス不一致):', modelUrl, err);

        // デフォルト（銀髪の女の子）以外のロード失敗時は、安全のためデフォルトを再試行
        const defaultUrl = '/deining/ulvm2_0001/ulvm2_0001.model3.json';
        if (modelUrl !== defaultUrl) {
            console.log('🔄 デフォルトモデル（銀髪の女の子）へのロードを再試行します...');
            localStorage.setItem('sys_avatar_pref', defaultUrl);
            try {
                const fallbackModel = await PIXI.live2d.Live2DModel.from(defaultUrl + '?v=' + Date.now());
                const guideEl = document.getElementById('deining-live2d-guidance');
                if (guideEl) guideEl.style.display = 'none';
                return fallbackModel;
            } catch (e2) { }
        }

        showLive2DGuidance();
        return null;
    }
}

function showLive2DGuidance() {
    const avatarContainer = document.getElementById('avatar-container') || document.body;
    let guideEl = document.getElementById('deining-live2d-guidance');

    // LAN / リモートアクセス環境ではLive2D利用規約の制限によりガイダンス表示を抑止
    const h = (window.location.hostname || '').toLowerCase();
    const isLocal = h === '127.0.0.1' || h === 'localhost' || h === '::1';
    if (!isLocal) {
        if (guideEl) guideEl.style.display = 'none';
        return;
    }

    // 動的ホバースタイルの注入 (初回のみ)
    if (!document.getElementById('deining-live2d-guidance-styles')) {
        const styleEl = document.createElement('style');
        styleEl.id = 'deining-live2d-guidance-styles';
        styleEl.textContent = `
            .deining-model-dl-btn:hover {
                background: rgba(168, 85, 247, 0.22) !important;
                border-color: rgba(168, 85, 247, 0.65) !important;
                transform: translateY(-1px);
            }
        `;
        document.head.appendChild(styleEl);
    }

    const renderGuidanceHtml = () => {
        const cachedAvatarData = localStorage.getItem('sys_avatar_pref') || '';
        const isBrown = cachedAvatarData.includes('fav_usaneko') || cachedAvatarData.includes('茶髪の癒し系お姉さん');

        return `
            <h3 style="font-size: 16px; font-weight: 700; color: #f8fafc; margin-bottom: 8px; letter-spacing: -0.01em;">Live2Dモデルが未配置です</h3>
            
            <div id="deining-drop-box" style="margin: 8px 0; padding: 16px 12px; background: rgba(0, 0, 0, 0.4); border: 2px dashed rgba(168, 85, 247, 0.6); border-radius: 14px; width: 100%; max-width: 320px; pointer-events: none; transition: all 0.2s ease;">
                <svg style="width: 26px; height: 26px; margin: 0 auto 6px auto; stroke: #c084fc; display: block;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
                <div style="font-size: 13px; font-weight: 600; color: #f1f5f9; margin-bottom: 3px;">ここにダウンロードしたZIPを<br>ドロップしてね！</div>
                <div style="font-size: 11px; color: #a5b4fc; text-decoration: underline;">（またはここをクリックしてファイルを選択）</div>
            </div>

            <div style="font-size: 11px; color: #c084fc; background: rgba(168, 85, 247, 0.15); padding: 3px 12px; border-radius: 9999px; border: 1px solid rgba(168, 85, 247, 0.3); margin-bottom: 10px;">
                銀髪の女の子 / 茶髪の癒し系お姉さん 専用
            </div>

            <div id="deining-model-links-container" style="width: 100%; max-width: 320px; display: flex; flex-direction: column; gap: 6px; text-align: left;">
                <div style="font-size: 11px; font-weight: 600; color: #94a3b8; display: flex; align-items: center; justify-content: space-between; padding: 0 4px;">
                    <span>モデル配布先（ダウンロード無料）</span>
                    <span style="font-size: 10px; color: #64748b;">vtem.jp</span>
                </div>

                <a href="https://vtem.jp/listing/model_0005_favorite/" target="_blank" rel="noopener noreferrer" class="deining-model-dl-btn" style="display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: rgba(30, 27, 75, 0.5); border: 1px solid ${isBrown ? 'rgba(168, 85, 247, 0.6)' : 'rgba(255, 255, 255, 0.12)'}; border-radius: 10px; color: #e2e8f0; text-decoration: none; font-size: 12px; transition: all 0.2s ease; cursor: pointer;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #d97706; box-shadow: 0 0 6px rgba(217, 119, 6, 0.8);"></span>
                        <span style="font-weight: 600;">茶髪の癒し系お姉さん</span>
                        ${isBrown ? '<span style="font-size: 9px; padding: 1px 6px; border-radius: 9999px; background: rgba(168, 85, 247, 0.35); color: #f3e8ff; border: 1px solid rgba(168, 85, 247, 0.5);">選択中</span>' : ''}
                    </div>
                    <span style="display: flex; align-items: center; gap: 3px; font-size: 11px; color: #c084fc;">
                        <span>配布ページ</span>
                        <svg style="width: 12px; height: 12px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
                    </span>
                </a>

                <a href="https://vtem.jp/listing/ulvm2_0001/" target="_blank" rel="noopener noreferrer" class="deining-model-dl-btn" style="display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: rgba(30, 27, 75, 0.5); border: 1px solid ${!isBrown ? 'rgba(168, 85, 247, 0.6)' : 'rgba(255, 255, 255, 0.12)'}; border-radius: 10px; color: #e2e8f0; text-decoration: none; font-size: 12px; transition: all 0.2s ease; cursor: pointer;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #cbd5e1; box-shadow: 0 0 6px rgba(203, 213, 225, 0.8);"></span>
                        <span style="font-weight: 600;">銀髪の女の子</span>
                        ${!isBrown ? '<span style="font-size: 9px; padding: 1px 6px; border-radius: 9999px; background: rgba(168, 85, 247, 0.35); color: #f3e8ff; border: 1px solid rgba(168, 85, 247, 0.5);">選択中</span>' : ''}
                    </div>
                    <span style="display: flex; align-items: center; gap: 3px; font-size: 11px; color: #c084fc;">
                        <span>配布ページ</span>
                        <svg style="width: 12px; height: 12px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
                    </span>
                </a>
            </div>
            
            <input type="file" id="deining-live2d-file-input" accept=".zip" style="display: none;" />
        `;
    };

    if (!guideEl) {
        guideEl = document.createElement('div');
        guideEl.id = 'deining-live2d-guidance';
        guideEl.style.cssText = `
            position: absolute;
            inset: 16px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            background: rgba(15, 23, 42, 0.90);
            backdrop-filter: blur(14px);
            border: 2px dashed rgba(168, 85, 247, 0.45);
            border-radius: 20px;
            color: #cbd5e1;
            padding: 20px 16px;
            text-align: center;
            z-index: 25;
            pointer-events: auto;
            cursor: pointer;
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
            user-select: none;
        `;
        guideEl.innerHTML = renderGuidanceHtml();

        // ドラッグホバー時の視覚的フィードバック（カウンター管理で内部要素通過時のチラつきを防止）
        let guideDragCounter = 0;
        guideEl.addEventListener('dragenter', (e) => {
            e.preventDefault();
            e.stopPropagation();
            guideDragCounter++;
            guideEl.style.borderColor = '#c084fc';
            guideEl.style.background = 'rgba(24, 16, 44, 0.96)';
            guideEl.style.boxShadow = '0 0 25px rgba(168, 85, 247, 0.45)';
            const dropBox = guideEl.querySelector('#deining-drop-box');
            if (dropBox) {
                dropBox.style.borderColor = '#e879f9';
                dropBox.style.background = 'rgba(168, 85, 247, 0.2)';
            }
        });
        guideEl.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
        });
        guideEl.addEventListener('dragleave', (e) => {
            e.preventDefault();
            e.stopPropagation();
            guideDragCounter--;
            if (guideDragCounter <= 0) {
                guideDragCounter = 0;
                guideEl.style.borderColor = 'rgba(168, 85, 247, 0.45)';
                guideEl.style.background = 'rgba(15, 23, 42, 0.90)';
                guideEl.style.boxShadow = '0 10px 30px rgba(0, 0, 0, 0.5)';
                const dropBox = guideEl.querySelector('#deining-drop-box');
                if (dropBox) {
                    dropBox.style.borderColor = 'rgba(168, 85, 247, 0.6)';
                    dropBox.style.background = 'rgba(0, 0, 0, 0.4)';
                }
            }
        });
        guideEl.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();
            guideDragCounter = 0;
            guideEl.style.borderColor = 'rgba(168, 85, 247, 0.45)';
            guideEl.style.background = 'rgba(15, 23, 42, 0.90)';
            guideEl.style.boxShadow = '0 10px 30px rgba(0, 0, 0, 0.5)';
            const dropBox = guideEl.querySelector('#deining-drop-box');
            if (dropBox) {
                dropBox.style.borderColor = 'rgba(168, 85, 247, 0.6)';
                dropBox.style.background = 'rgba(0, 0, 0, 0.4)';
            }

            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                const file = e.dataTransfer.files[0];
                if (typeof window.deiningUploadModelZip === 'function') {
                    window.deiningUploadModelZip(file);
                } else {
                    console.error('[Deining Live2D] deiningUploadModelZip is not available');
                }
            }
        });

        // クリックでファイル選択（配布リンククリック時はファイルダイアログを開かない）
        guideEl.addEventListener('click', (e) => {
            if (e.target.closest('a')) {
                return;
            }
            const input = document.getElementById('deining-live2d-file-input');
            if (input) input.click();
        });

        guideEl.addEventListener('change', (e) => {
            if (e.target && e.target.id === 'deining-live2d-file-input') {
                if (e.target.files && e.target.files.length > 0) {
                    const file = e.target.files[0];
                    if (typeof window.deiningUploadModelZip === 'function') {
                        window.deiningUploadModelZip(file);
                    }
                    e.target.value = '';
                }
            }
        });

        avatarContainer.appendChild(guideEl);
    } else {
        // 既存のガイダンス要素がある場合、選択モデル状態に応じてHTMLを最新化
        guideEl.innerHTML = renderGuidanceHtml();
    }
    guideEl.style.display = 'flex';
}

let _avatarMounted = false;
async function mountAvatarSystem() {
    const curPref = localStorage.getItem('sys_avatar_pref');
    if (curPref && curPref.startsWith('/deining/vrm/')) {
        return; // VRM選択中はLive2Dを自動起動せず、sys_avatar_prefの上書きも抑止
    }
    if (_avatarMounted) return;
    _avatarMounted = true;
    try {
        const canvas = document.getElementById('live2d-canvas');
        const container = canvas ? canvas.parentElement : document.body;
        const app = new PIXI.Application({
            view: canvas,
            autoStart: true,
            resizeTo: container,
            transparent: true,
            backgroundAlpha: 0,
            resolution: window.devicePixelRatio || 1,
            autoDensity: true
        });

        // バックエンドから自動検知されたLive2Dモデル一覧を取得
        let detectedModels = [];
        try {
            const mRes = await fetch('http://127.0.0.1:5000/api/avatar/models');
            if (mRes.ok) {
                const mData = await mRes.json();
                detectedModels = mData.models || [];
            }
        } catch (e) { }

        let cachedAvatarData = localStorage.getItem('sys_avatar_pref');
        const isBrown = cachedAvatarData && (cachedAvatarData.includes('fav_usaneko') || cachedAvatarData.includes('茶髪の癒し系お姉さん'));
        const activeModelInfo = detectedModels.find(m => isBrown ? m.id === 'brown' : m.id === 'silver');
        if (activeModelInfo && activeModelInfo.url) {
            cachedAvatarData = activeModelInfo.url;
            localStorage.setItem('sys_avatar_pref', cachedAvatarData);
        } else if (!cachedAvatarData || !cachedAvatarData.startsWith('/deining/')) {
            cachedAvatarData = '/deining/ulvm2_0001/ulvm2_0001.model3.json';
            localStorage.setItem('sys_avatar_pref', cachedAvatarData);
        }
        window.deiningAvatarCore.setLive2dActive = (active) => {
            if (app && app.ticker) {
                if (active) {
                    if (!app.ticker.started) app.ticker.start();
                } else {
                    if (app.ticker.started) app.ticker.stop();
                }
            }
        };

        function clearAllLive2DModels() {
            if (!app || !app.stage) return;
            const modelsToRemove = [];
            for (const child of app.stage.children) {
                if (child && (child._isLive2DModel || child.internalModel || (child.constructor && child.constructor.name === 'Live2DModel') || child === sysAvatarCtx)) {
                    modelsToRemove.push(child);
                }
            }
            for (const m of modelsToRemove) {
                try {
                    app.stage.removeChild(m);
                    m.destroy({ children: true });
                } catch (e) {
                    console.warn('[Deining Live2D] モデル破棄中ワーニング:', e);
                }
            }
            sysAvatarCtx = null;
        }

        let _currentModelLoadSeq = 0;

        async function switchModelInternal(newModelUrl) {
            const loadSeq = ++_currentModelLoadSeq;
            localStorage.setItem('sys_avatar_pref', newModelUrl);

            // ロード開始前に既存モデルをクリーンアップ
            clearAllLive2DModels();

            const loadedModel = await safeLoadLive2DModel(newModelUrl, app);
            
            // 待機中に別のモデル読み込みが要求されていた場合、古い要求の結果は破棄
            if (loadSeq !== _currentModelLoadSeq) {
                console.log(`[Deining Live2D] 重複または古いモデル要求(seq=${loadSeq})を破棄します(最新seq=${_currentModelLoadSeq})`);
                if (loadedModel) {
                    try { loadedModel.destroy({ children: true }); } catch (e) {}
                }
                return;
            }

            if (loadedModel) {
                // ステージ追加直前にも既存モデルを確実にゼロにしてから追加（二重表示の完全防止）
                clearAllLive2DModels();
                loadedModel._isLive2DModel = true;
                sysAvatarCtx = loadedModel;
                app.stage.addChild(sysAvatarCtx);
                injectAvatarMods(sysAvatarCtx);
                if (typeof adjustAvatarScale === 'function') adjustAvatarScale();
                if (typeof window.loadTouchZones === 'function') window.loadTouchZones();
            }
        }

        const modelSelect = document.getElementById('live2d-b-engine__select');
        if (modelSelect) {
            modelSelect.value = cachedAvatarData;
            modelSelect.addEventListener('change', async (e) => {
                await switchModelInternal(e.target.value);
            });
        }

        // グローバルにモデル切り替え関数を公開
        window.deiningAvatarCore.changeModel = switchModelInternal;

        // 初期モデルを安全に読み込み
        await switchModelInternal(cachedAvatarData);
        // コンテナのサイズ変更時にモデルのスケールと位置を調整
        function adjustAvatarScale() {
            if (!sysAvatarCtx) return;

            // Pixiの描画領域サイズを即座に更新
            app.resize();

            // 一旦スケールを1.0に戻して、モデル本来のサイズを取得
            sysAvatarCtx.scale.set(1, 1);
            const originalWidth = sysAvatarCtx.width;
            const originalHeight = sysAvatarCtx.height;

            // Pixiの論理サイズ（CSSピクセルサイズ）を取得
            // アバターのスケール計算用の幅（はみ出しを考慮した本来のコンテナ幅）
            const avatarContainer = document.getElementById('avatar-container');
            const targetWidth = avatarContainer ? avatarContainer.clientWidth : app.screen.width;
            const targetHeight = app.screen.height;

            if (originalWidth === 0 || originalHeight === 0 || targetWidth === 0 || targetHeight === 0) return;

            // 画面に収まる基本スケールを計算
            const scaleX = targetWidth / originalWidth;
            const scaleY = targetHeight / originalHeight;

            // ユーザーの理想のクローズアップ（上半身拡大）表示を実現するため、2.1倍ズームを適用
            let zoomScale = Math.min(scaleX, scaleY) * 2.1;
            let offsetY = - (originalHeight * zoomScale) * 0.05;

            const cachedAvatarData = localStorage.getItem('sys_avatar_pref') || '';
            if (cachedAvatarData.includes('ulvm2') || cachedAvatarData.includes('銀髪の女の子') || !cachedAvatarData) {
                // 銀髪の女の子: 1.3倍の程よいアップ感で、顔が綺麗に映る位置へ引き上げ
                zoomScale *= 1.3;
                offsetY = - (originalHeight * zoomScale) * 0.06;
                console.log('🌸 [Live2D Scale] 銀髪の女の子: 1.3倍拡大適用, offsetY =', offsetY);
            } else if (cachedAvatarData.includes('fav_usaneko') || cachedAvatarData.includes('茶髪の癒し系お姉さん')) {
                // 茶髪の癒し系お姉さん: 銀髪と同様に顔と首元が綺麗に映るよう1.18倍拡大し、上部へ引き上げ
                zoomScale *= 1.18;
                offsetY = - (originalHeight * zoomScale) * 0.055;
                console.log('🌸 [Live2D Scale] 茶髪の癒し系お姉さん: 1.18倍拡大適用, offsetY =', offsetY);
            }

            sysAvatarCtx.scale.set(zoomScale);

            // 左右中央に配置（実際のキャンバス幅 app.screen.width を基準にする）
            sysAvatarCtx.x = (app.screen.width - originalWidth * zoomScale) / 2;
            // 顔と上半身が綺麗に映るようにY軸を調整
            sysAvatarCtx.y = offsetY;
        }
        // リサイズイベントの監視
        window.addEventListener('resize', adjustAvatarScale);
        adjustAvatarScale();

        // --- マウス追従（目線・顔の向き） ---
        const canvasEl = document.getElementById('live2d-canvas');
        window.deiningAvatarCore = window.deiningAvatarCore || {};
        if (window.deiningAvatarCore.isMouseTrackingActive === undefined) {
            window.deiningAvatarCore.isMouseTrackingActive = true;
        }
        window.deiningAvatarCore.setMouseTrackingMode = (active) => {
            console.log(`[Live2D] setMouseTrackingMode: ${active}`);
            window.deiningAvatarCore.isMouseTrackingActive = active;
            if (!active && sysAvatarCtx && sysAvatarCtx.internalModel && sysAvatarCtx.internalModel.focusController) {
                sysAvatarCtx.internalModel.focusController.focus(0, 0);
            }
        };

        let isPointerDown = false;
        let interactionMetric = 0;
        let lastPetX = 0;
        let lastPetY = 0;
        let lastMessageSentTime = 0;

        // --- Hitbox Edit Logic ---
        const hitboxesContainer = document.getElementById('touchZones-container');
        let isHitboxEditModeActive = false;
        window.deiningAvatarCore = window.deiningAvatarCore || {};
        window.deiningAvatarCore.setHitboxEditMode = (active) => {
            isHitboxEditModeActive = active;
            // パネルの表示状態などはSvelte側で管理するが、キャンバスの枠線再描画を呼び出す
            const overlays = document.querySelectorAll('.hitbox-overlay');
            overlays.forEach(overlay => {
                overlay.style.display = active ? 'block' : 'none';
            });
        };

        // 銀髪の女の子用デフォルト
        const defaultHitboxesSilver = [
            { id: "head", name: "頭", type: "pat", message: "（頭を撫でる）", expression: "blush", rect: { left: 37.0667, top: 4.668556, width: 25.4667, height: 12.4495 } },
            { id: "arm", name: "左腕", type: "click", message: "（腕を触る）", expression: "surprised", rect: { left: 29.5333, top: 38.1063, width: 8.5318, height: 24.2307 } },
            { id: "arm_r", name: "右腕", type: "click", message: "（腕を触る）", expression: "surprised", rect: { left: 62.5333, top: 35.2203, width: 8.5333, height: 27.4555 } }
        ];

        // 茶髪の癒し系お姉さん用デフォルト
        const defaultHitboxesBrown = [
            { id: "head", name: "頭", type: "pat", message: "（頭を撫でる）", expression: "blush", rect: { left: 39.4668, top: 8.0000, width: 23.0652, height: 11.6701 } },
            { id: "arm", name: "左腕", type: "click", message: "（腕を触る）", expression: "surprised", rect: { left: 31.5333, top: 36.8000, width: 7.7333, height: 32.0104 } },
            { id: "arm_r", name: "右腕", type: "click", message: "（腕を触る）", expression: "surprised", rect: { left: 61.0666, top: 34.2000, width: 9.2000, height: 35.1240 } }
        ];

        let touchZones = [];

        const getHitboxKey = () => 'sys_touchzones_' + (localStorage.getItem('sys_avatar_pref') || 'default');

        window.loadTouchZones = function () {
            let saved = localStorage.getItem(getHitboxKey());
            const currentPref = localStorage.getItem('sys_avatar_pref') || '';
            const fallbackHitboxes = (currentPref.includes('fav_usaneko') || currentPref.includes('茶髪の癒し系お姉さん')) ? defaultHitboxesBrown : defaultHitboxesSilver;

            try {
                touchZones = saved ? JSON.parse(saved) : null;
                if (!Array.isArray(touchZones)) throw new Error();
            } catch (e) {
                touchZones = JSON.parse(JSON.stringify(fallbackHitboxes));
            }

            // Validation
            touchZones.forEach(hb => {
                if (!hb.id) hb.id = Date.now() + Math.random().toString(36).substr(2, 5);
                if (!hb.rect) hb.rect = { left: 0, top: 0, width: 20, height: 20 };
                if (hb.rect.width < 5) hb.rect.width = 20;
                if (hb.rect.height < 5) hb.rect.height = 20;
                if (hb.expression === 'heart') hb.expression = 'blush';
            });

            renderHitboxes();
            renderHitboxConfigPanel();
        };

        function saveHitboxes() {
            localStorage.setItem(getHitboxKey(), JSON.stringify(touchZones));
        }

        function renderHitboxes() {
            if (!hitboxesContainer) return;
            hitboxesContainer.innerHTML = '';
            touchZones.forEach(hb => {
                const div = document.createElement('div');
                div.className = 'hitbox-overlay' + (hb.type === 'click' ? ' click-type' : '');
                div.dataset.id = hb.id;
                div.style.left = hb.rect.left + '%';
                div.style.top = hb.rect.top + '%';
                div.style.width = hb.rect.width + '%';
                div.style.height = hb.rect.height + '%';
                if (isHitboxEditModeActive) {
                    div.style.display = 'block';
                    if (currentDraggedHitboxId === hb.id) div.classList.add('selected');
                } else {
                    div.style.display = 'none';
                }
                hitboxesContainer.appendChild(div);
            });
        }

        function renderHitboxConfigPanel() {
            const listContainer = document.getElementById('hitbox-list-container');
            if (!listContainer) return;
            listContainer.innerHTML = '';

            touchZones.forEach(hb => {
                const item = document.createElement('div');
                item.className = 'hitbox-item';
                if (currentDraggedHitboxId === hb.id) item.style.borderColor = 'lime';

                const row1 = document.createElement('div');
                row1.className = 'hitbox-item-row';
                row1.innerHTML = `
                            <input type="text" value="${hb.name}" style="width:60px;" placeholder="名前" onchange="modifyTouchZone('${hb.id}', 'name', this.value)">
                            <select onchange="modifyTouchZone('${hb.id}', 'type', this.value)">
                                <option value="pat" ${hb.type === 'pat' ? 'selected' : ''}>なでなで</option>
                                <option value="click" ${hb.type === 'click' ? 'selected' : ''}>クリック</option>
                            </select>
                            <select onchange="modifyTouchZone('${hb.id}', 'expression', this.value)" style="width:60px;">
                                <option value="idle" ${hb.expression === 'idle' ? 'selected' : ''}>通常</option>
                                <option value="blush" ${hb.expression === 'blush' ? 'selected' : ''}>照れ</option>
                                <option value="surprised" ${hb.expression === 'surprised' ? 'selected' : ''}>驚き</option>
                                <option value="smile" ${hb.expression === 'smile' ? 'selected' : ''}>笑顔</option>
                                <option value="angry" ${hb.expression === 'angry' ? 'selected' : ''}>怒り</option>
                                <option value="cry" ${hb.expression === 'cry' ? 'selected' : ''}>泣き</option>
                                <option value="sleep" ${hb.expression === 'sleep' ? 'selected' : ''}>眠い</option>
                                <option value="xd" ${hb.expression === 'xd' ? 'selected' : ''}>にこ</option>
                            </select>
                            <button class="icon-btn" style="padding:2px; font-size:10px; color:#ff6b6b;" onclick="removeTouchZone('${hb.id}')">削除</button>
                        `;
                const row2 = document.createElement('div');
                row2.className = 'hitbox-item-row';
                row2.innerHTML = `
                            <span style="font-size:10px;">送信文:</span>
                            <input type="text" value="${hb.message}" placeholder="（撫でる）" onchange="modifyTouchZone('${hb.id}', 'message', this.value)">
                        `;
                item.appendChild(row1);
                item.appendChild(row2);
                listContainer.appendChild(item);
            });
        }

        window.modifyTouchZone = function (id, key, value) {
            const hb = touchZones.find(h => h.id === id);
            if (hb) {
                hb[key] = value;
                saveHitboxes();
                renderHitboxes();
            }
        };

        window.removeTouchZone = function (id) {
            touchZones = touchZones.filter(h => h.id !== id);
            saveHitboxes();
            renderHitboxes();
            renderHitboxConfigPanel();
        };

        window.deiningAvatarCore = window.deiningAvatarCore || {};
        window.deiningAvatarCore.refreshHitboxEditor = () => {
            if (typeof renderHitboxConfigPanel === 'function') {
                renderHitboxConfigPanel();
            }
        };
        window.deiningAvatarCore.addHitbox = () => {
            touchZones.push({
                id: Date.now().toString(),
                name: "新規",
                type: "click",
                message: "（触れる）",
                expression: "idle",
                rect: { left: 40, top: 40, width: 20, height: 20 }
            });
            saveHitboxes();
            renderHitboxes();
            renderHitboxConfigPanel();
        };

        const addBtn = document.getElementById('add-hitbox-btn');
        if (addBtn) {
            addBtn.addEventListener('click', window.deiningAvatarCore.addHitbox);
        }

        let isDraggingHitbox = false;
        let currentDraggedHitboxId = null;
        let dragStartX, dragStartY, initialLeft, initialTop;

        window.loadTouchZones();

        // Hitbox editing events are handled via isHitboxEditModeActive variable directly

        hitboxesContainer.addEventListener('mousedown', (e) => {
            if (!isHitboxEditModeActive) return;
            const overlay = e.target.closest('.hitbox-overlay');
            if (!overlay) return;

            currentDraggedHitboxId = overlay.dataset.id;

            // Update selection visually without destroying elements (which breaks the mousedown event)
            document.querySelectorAll('.hitbox-overlay').forEach(el => el.classList.remove('selected'));
            overlay.classList.add('selected');
            renderHitboxConfigPanel();

            const rect = overlay.getBoundingClientRect();
            if (e.clientX > rect.right - 30 && e.clientY > rect.bottom - 30) return; // Resize

            // Before drag, update width/height
            const canvasEl = document.getElementById('live2d-canvas');
            const containerRect = (canvasEl ? canvasEl.parentElement : document.body).getBoundingClientRect();
            const hb = touchZones.find(h => h.id === currentDraggedHitboxId);
            if (hb) {
                hb.rect.width = (rect.width / containerRect.width) * 100;
                hb.rect.height = (rect.height / containerRect.height) * 100;
            }

            isDraggingHitbox = true;
            dragStartX = e.clientX;
            dragStartY = e.clientY;
            initialLeft = parseFloat(overlay.style.left) || 0;
            initialTop = parseFloat(overlay.style.top) || 0;
            e.stopPropagation();
        });

        hitboxesContainer.addEventListener('mousedown', (e) => {
            if (!isHitboxEditModeActive) return;
            const overlay = e.target.closest('.hitbox-overlay');
            if (!overlay) return;

            currentDraggedHitboxId = overlay.dataset.id;

            // Update selection visually without destroying elements (which breaks the mousedown event)
            document.querySelectorAll('.hitbox-overlay').forEach(el => el.classList.remove('selected'));
            overlay.classList.add('selected');
            renderHitboxConfigPanel();

            const rect = overlay.getBoundingClientRect();
            if (e.clientX > rect.right - 20 && e.clientY > rect.bottom - 20) return; // Resize

            // Before drag, update width/height
            const canvasEl = document.getElementById('live2d-canvas');
            const containerRect = (canvasEl ? canvasEl.parentElement : document.body).getBoundingClientRect();
            const hb = touchZones.find(h => h.id === currentDraggedHitboxId);
            if (hb) {
                hb.rect.width = (rect.width / containerRect.width) * 100;
                hb.rect.height = (rect.height / containerRect.height) * 100;
            }

            isDraggingHitbox = true;
            dragStartX = e.clientX;
            dragStartY = e.clientY;
            initialLeft = parseFloat(overlay.style.left) || 0;
            initialTop = parseFloat(overlay.style.top) || 0;
            e.stopPropagation();
        });

        window.addEventListener('mousemove', (e) => {
            if (isDraggingHitbox && isHitboxEditModeActive && currentDraggedHitboxId) {
                const hb = touchZones.find(h => h.id === currentDraggedHitboxId);
                if (!hb) return;
                const canvasEl = document.getElementById('live2d-canvas');
                const containerRect = (canvasEl ? canvasEl.parentElement : document.body).getBoundingClientRect();
                const dx = ((e.clientX - dragStartX) / containerRect.width) * 100;
                const dy = ((e.clientY - dragStartY) / containerRect.height) * 100;

                hb.rect.left = Math.min(Math.max(0, initialLeft + dx), 100 - hb.rect.width);
                hb.rect.top = Math.min(Math.max(0, initialTop + dy), 100 - hb.rect.height);

                const overlay = document.querySelector(`.hitbox-overlay[data-id="${hb.id}"]`);
                if (overlay) {
                    overlay.style.left = hb.rect.left + '%';
                    overlay.style.top = hb.rect.top + '%';
                }
            }
        });

        window.addEventListener('mouseup', () => {
            if (isHitboxEditModeActive && currentDraggedHitboxId) {
                const canvasEl = document.getElementById('live2d-canvas');
                const containerRect = (canvasEl ? canvasEl.parentElement : document.body).getBoundingClientRect();
                const overlay = document.querySelector(`.hitbox-overlay[data-id="${currentDraggedHitboxId}"]`);
                const hb = touchZones.find(h => h.id === currentDraggedHitboxId);
                if (overlay && hb) {
                    const overlayRect = overlay.getBoundingClientRect();
                    hb.rect.width = (overlayRect.width / containerRect.width) * 100;
                    hb.rect.height = (overlayRect.height / containerRect.height) * 100;
                }
                isDraggingHitbox = false;
                saveHitboxes();
            }
        });

        function isPointerOverChatOrUI(event) {
            if (!event) return false;

            // 1. チャット画面 (.chat-screen) の境界矩形判定
            // モバイル画面では下部53dvhに配置されており、Hitbox判定はこのチャット画面より下層に位置する
            const chatScreenEl = document.querySelector('.chat-screen');
            if (chatScreenEl) {
                const csRect = chatScreenEl.getBoundingClientRect();
                if (csRect.width > 0 && csRect.height > 0) {
                    if (event.clientX >= csRect.left && event.clientX <= csRect.right &&
                        event.clientY >= csRect.top && event.clientY <= csRect.bottom) {
                        return true;
                    }
                }
            }

            // 2. target要素のチェック（ボタン、入力欄、ヘッダー、サイドバー等）
            const target = event.target;
            if (target && target instanceof Element) {
                if (target.closest('.chat-screen') ||
                    target.closest('header, nav, aside, [role="dialog"]') ||
                    target.closest('button, input, textarea, select, [role="button"]') ||
                    target.closest('.live2d-controls, #live2d-controls-container, #chat-sidebar')) {
                    return true;
                }
            }

            // 3. elementsFromPoint による重なり要素チェック
            if (typeof document.elementsFromPoint === 'function') {
                try {
                    const elements = document.elementsFromPoint(event.clientX, event.clientY);
                    for (const el of elements) {
                        if (el.closest('.chat-screen') ||
                            el.closest('header, nav, aside, [role="dialog"]') ||
                            el.closest('button, input, textarea, select, [role="button"]') ||
                            el.closest('.live2d-controls, #live2d-controls-container, #chat-sidebar')) {
                            return true;
                        }
                    }
                } catch (_) {}
            }

            return false;
        }

        // --- Interaction & Tracking Logic ---
        let pointerDownX = 0, pointerDownY = 0;
        let isHitboxPointerDown = false;
        let currentFocusX = 0, currentFocusY = 0;
        let targetFocusX = 0, targetFocusY = 0;
        let isTouching = false;
        let isMouseInWindow = false;
        let touchReleaseTimeout = null;
        let gyroFocusX = 0, gyroFocusY = 0;
        let hasGyro = false;

        // モバイル端末の傾き（ジャイロ）による自然追従
        let gyroPermissionRequested = false;
        function tryRequestGyroPermission() {
            if (gyroPermissionRequested) return;
            if (typeof window !== 'undefined' && typeof window.DeviceOrientationEvent !== 'undefined' && typeof window.DeviceOrientationEvent.requestPermission === 'function') {
                gyroPermissionRequested = true;
                window.DeviceOrientationEvent.requestPermission().then(state => {
                    if (state === 'granted') {
                        // 権限許可された場合、後続の deviceorientation イベントが受信可能になる
                    }
                }).catch(() => {});
            }
        }

        if (typeof window !== 'undefined' && 'DeviceOrientationEvent' in window) {
            try {
                window.addEventListener('deviceorientation', (e) => {
                    if (e.gamma === null || e.beta === null) return;
                    hasGyro = true;
                    // gamma: 左右の傾き（-30度〜+30度を -0.6〜+0.6 に正規化）
                    const gx = Math.max(-1, Math.min(1, (e.gamma || 0) / 30));
                    // beta: 前後の傾き（基準角45度からの偏差 -30度〜+30度を -0.6〜+0.6 に正規化）
                    const gy = Math.max(-1, Math.min(1, ((e.beta || 45) - 45) / 30));
                    gyroFocusX = gx * 0.55;
                    gyroFocusY = -gy * 0.55;
                }, { passive: true });
            } catch (_) {}
        }

        // 自然追従・アイドリング呼吸・スムーズ視線復帰のアニメーションループ
        let idleBreathTime = 0;
        function updateNaturalFocus() {
            if (!sysAvatarCtx || !sysAvatarCtx.internalModel || !sysAvatarCtx.internalModel.focusController) {
                requestAnimationFrame(updateNaturalFocus);
                return;
            }

            if (!window.deiningAvatarCore || !window.deiningAvatarCore.isMouseTrackingActive) {
                sysAvatarCtx.internalModel.focusController.focus(0, 0);
                requestAnimationFrame(updateNaturalFocus);
                return;
            }

            idleBreathTime += 0.02;
            // 自然な視線揺らぎ（アイドリング呼吸）
            const breathX = Math.sin(idleBreathTime * 0.7) * 0.025;
            const breathY = Math.cos(idleBreathTime * 0.5) * 0.02;

            if (isMouseInWindow || isTouching) {
                // マウスが画面内にある時、またはタッチ操作中は、最後に追従した目標座標を維持したまま自然な呼吸揺らぎを重畳
                currentFocusX += ((targetFocusX + breathX) - currentFocusX) * 0.12;
                currentFocusY += ((targetFocusY + breathY) - currentFocusY) * 0.12;
            } else if (hasGyro) {
                // スマートフォンのジャイロセンサー連動時
                currentFocusX += ((gyroFocusX + breathX) - currentFocusX) * 0.12;
                currentFocusY += ((gyroFocusY + breathY) - currentFocusY) * 0.12;
            } else {
                // マウスが画面外（初期状態またはpointerleave後）またはタッチ終了後：スムーズに正面へ復帰
                currentFocusX += (breathX - currentFocusX) * 0.12;
                currentFocusY += (breathY - currentFocusY) * 0.12;
            }

            sysAvatarCtx.internalModel.focusController.focus(currentFocusX, currentFocusY);
            requestAnimationFrame(updateNaturalFocus);
        }
        requestAnimationFrame(updateNaturalFocus);

        function updateFocusFromPointer(clientX, clientY) {
            if (!canvasEl) return;
            const rect = canvasEl.getBoundingClientRect();
            const faceX = rect.left + rect.width / 2;
            const faceY = rect.top + rect.height * 0.3;

            let fx = (clientX - faceX) / (rect.width / 2);
            let fy = -(clientY - faceY) / (rect.height / 2);
            targetFocusX = Math.max(-1, Math.min(1, fx));
            targetFocusY = Math.max(-1, Math.min(1, fy));
        }

        window.addEventListener('pointerdown', (event) => {
            if (window.deiningAvatarCore && window.deiningAvatarCore.isModelVisible === false) return;
            unlockAudio();
            tryRequestGyroPermission();
            isPointerDown = true;
            isTouching = true;
            if (touchReleaseTimeout) {
                clearTimeout(touchReleaseTimeout);
                touchReleaseTimeout = null;
            }
            pointerDownX = event.clientX;
            pointerDownY = event.clientY;
            lastPetX = event.clientX;
            lastPetY = event.clientY;

            // チャット画面や前面UIの上で操作が開始された場合はHitbox判定の対象外とする
            isHitboxPointerDown = !isPointerOverChatOrUI(event);
            if (isHitboxPointerDown) {
                updateFocusFromPointer(event.clientX, event.clientY);
            }
        });

        window.addEventListener('pointerup', (event) => {
            if (window.deiningAvatarCore && window.deiningAvatarCore.isModelVisible === false) return;
            const wasHitboxPointerDown = isHitboxPointerDown;
            isPointerDown = false;
            isHitboxPointerDown = false;

            // タッチを離した後の自然な視線復帰（1.2秒後にアイドリング/ジャイロへ滑らかに戻る）
            if (touchReleaseTimeout) clearTimeout(touchReleaseTimeout);
            touchReleaseTimeout = setTimeout(() => {
                isTouching = false;
            }, 1200);

            if (!sysAvatarCtx || !sysAvatarCtx.internalModel || !canvasEl || canvasEl.offsetParent === null) return;
            // 操作開始時または終了時にチャット画面/UI上にあった場合はHitboxクリックを発火しない
            if (!wasHitboxPointerDown || isPointerOverChatOrUI(event)) return;

            // Click detection
            const deltaX = Math.abs(event.clientX - pointerDownX);
            const deltaY = Math.abs(event.clientY - pointerDownY);
            if (deltaX < 10 && deltaY < 10) {
                const canvasEl = document.getElementById('live2d-canvas');
                const containerEl = canvasEl ? canvasEl.parentElement : document.body;
                const rect = containerEl.getBoundingClientRect();
                const px = ((event.clientX - rect.left) / rect.width) * 100;
                const py = ((event.clientY - rect.top) / rect.height) * 100;

                // Check click type touchZones (reverse order so top-most visually gets clicked first)
                const clickedHitbox = [...touchZones].reverse().find(hb => hb.type === 'click' &&
                    px >= hb.rect.left && px <= (hb.rect.left + hb.rect.width) &&
                    py >= hb.rect.top && py <= (hb.rect.top + hb.rect.height)
                );

                if (clickedHitbox) {
                    // Reaction
                    const expr = clickedHitbox.expression || 'surprised';
                    if (activeExpression !== expr && activeExpression !== 'heart') {
                        applyAvatarExpression(expr, 3000);
                    }

                    const now = Date.now();
                    const canSend = typeof window.canSendChatMessage === 'function'
                        ? window.canSendChatMessage()
                        : !(typeof window.getIsGenerating === 'function' && window.getIsGenerating()) && !isSpeaking && !isProcessingTts && (!ttsQueue || ttsQueue.length === 0);

                    if (now - lastMessageSentTime > 5000) {
                        if (typeof window.sendChatMessage === 'function' && canSend) {
                            window.sendChatMessage(clickedHitbox.message, true);
                            lastMessageSentTime = now;
                        }
                    }
                }
            }
        });

        window.addEventListener('pointercancel', () => {
            isPointerDown = false;
            isHitboxPointerDown = false;
            interactionMetric = 0;
            if (touchReleaseTimeout) clearTimeout(touchReleaseTimeout);
            touchReleaseTimeout = setTimeout(() => {
                isTouching = false;
            }, 500);
        });

        window.addEventListener('pointermove', (event) => {
            if (window.deiningAvatarCore && window.deiningAvatarCore.isModelVisible === false) return;
            if (!sysAvatarCtx || !sysAvatarCtx.internalModel || !canvasEl || canvasEl.offsetParent === null) return;

            // なでなで判定
            if (isPointerDown && isHitboxPointerDown && !isPointerOverChatOrUI(event)) {
                const canvasEl = document.getElementById('live2d-canvas');
                const containerEl = canvasEl ? canvasEl.parentElement : document.body;
                const rect = containerEl.getBoundingClientRect();
                const px = ((event.clientX - rect.left) / rect.width) * 100;
                const py = ((event.clientY - rect.top) / rect.height) * 100;

                // Find intersected 'pat' hitbox
                const activePatHitbox = touchZones.find(hb => hb.type === 'pat' &&
                    px >= hb.rect.left && px <= (hb.rect.left + hb.rect.width) &&
                    py >= hb.rect.top && py <= (hb.rect.top + hb.rect.height)
                );

                // Debug info
                const debugInfo = document.getElementById('pet-debug-info');
                if (debugInfo) {
                    const intersectInfo = activePatHitbox ? activePatHitbox.name : 'None';
                    debugInfo.innerHTML = `px: ${px.toFixed(1)}%, py: ${py.toFixed(1)}%<br>` +
                        `Score: ${interactionMetric.toFixed(0)}<br>` +
                        `Active Pat: ${intersectInfo}`;
                }

                if (activePatHitbox) {
                    let deltaMove = Math.abs(event.clientX - lastPetX) + Math.abs(event.clientY - lastPetY);
                    if (deltaMove > 3) { // モバイルタッチ向けに感度向上
                        interactionMetric += deltaMove;
                        lastPetX = event.clientX;
                        lastPetY = event.clientY;

                        if (interactionMetric > 220) { // なでなで発火しきい値
                            interactionMetric = 0; // Reset score
                            const expr = activePatHitbox.expression || 'blush';
                            if (activeExpression !== expr && activeExpression !== 'heart') {
                                applyAvatarExpression(expr, 3000);

                                const now = Date.now();
                                const canSend = typeof window.canSendChatMessage === 'function'
                                    ? window.canSendChatMessage()
                                    : !(typeof window.getIsGenerating === 'function' && window.getIsGenerating()) && !isSpeaking && !isProcessingTts && (!ttsQueue || ttsQueue.length === 0);

                                if (now - lastMessageSentTime > 5000) {
                                    if (typeof window.sendChatMessage === 'function' && canSend) {
                                        window.sendChatMessage(activePatHitbox.message, true);
                                        lastMessageSentTime = now;
                                    }
                                }
                            } else if (activeExpression === expr) {
                                // 既に同じ表情なら自動解除タイマーを3秒延長（メッセージは再送しない）
                                scheduleExpressionAutoReset(3000);
                            }
                        }
                    }
                }
            } else {
                if (interactionMetric > 0) interactionMetric -= 3;
            }

            // 視線追従のターゲット更新（マウスホバーまたはタッチ中）
            if (window.deiningAvatarCore.isMouseTrackingActive) {
                if (event.pointerType === 'mouse' || (isPointerDown && isHitboxPointerDown)) {
                    isMouseInWindow = true;
                    updateFocusFromPointer(event.clientX, event.clientY);
                }
            }
        });

        document.addEventListener('pointerleave', () => {
            isMouseInWindow = false;
            if (!isTouching) {
                targetFocusX = 0;
                targetFocusY = 0;
            }
        });
        // （リップシンク・まばたき用メソッドオーバーライドは抽出済み）
        console.log("🌸 Live2D モデルのロードに成功しました！");

        // 初期状態として安全にデフォルト(idle)状態を適用
        applyAvatarExpression('idle');
        console.log("🌸 Live2D 初期状態(idle)の設定が完了しました！");
    } catch (e) {
        console.error("❌ Live2D モデルのロードに失敗:", e);
    }
}
// テキスト解析による感情・表情のタイムライン生成
function parseSentimentTimeline(text) {
    if (!sysAvatarCtx) return [];

    const rules = [
        { exp: 'heart', keywords: ['えっち', '変態', 'エッチ', '///', 'ドキドキ', '気持ち良い', '好き', '♡', '❤️', '💕', '💖', '💗', '💓', '💘'] },
        { exp: 'blush', keywords: ['照れ', '恥ずかしい', 'モジモジ', 'はずかしい', '♡', '😳', '🥰', '😚', '😳💕'] },
        { exp: 'xd', keywords: ['笑顔', 'あはは', 'たのしい', '楽しい', 'やったー', 'にこにこ', 'わーい', 'わあ', '😆', '🤣', '✨', '✨️'] },
        { exp: 'smile', keywords: ['ふふ', 'えへへ', 'なるほど', 'うんうん', '微笑', '笑', '😊', '☺️', '🙂', '😋', '😄', '😁'] },
        { exp: 'angry', keywords: ['ぷんぷん', 'バカ', 'ダメ', 'うるさい', '怒るよ', 'むー', '怒', '💢', '😡', '😠', '😤'] },
        { exp: 'cry', keywords: ['悲', '泣', 'つらい', '寂しい', 'ごめん', 'ごめんね', '怖い', 'えーん', '痛', '😢', '😭', '🥺', '😿'] },
        { exp: 'surprised', keywords: ['驚', 'えっ', '本当に', '！？', 'びっくり', 'まさか', '😦', '💦', '😲', '😮', '😱', '🙀', '❗', '⁉️'] },
        { exp: 'sleep', keywords: ['眠い', 'ふぁあ', 'おやすみ', 'ねむい', 'ウトウト', '😴', '💤', '🥱'] },
        { exp: 'question', keywords: ['はてな', '疑問', 'なぜ', 'どうして', 'かしら', 'のかな？', '🤔', '❓', '❔'] }
    ];

    let events = [];
    const textLen = text.length;
    if (textLen === 0) return events;

    for (const rule of rules) {
        for (const kw of rule.keywords) {
            if (!kw) continue;
            let idx = 0;
            while ((idx = text.indexOf(kw, idx)) !== -1) {
                events.push({ ratio: idx / textLen, exp: rule.exp, kw: kw });
                idx += kw.length;
            }
        }
    }

    // 割合順（登場順）にソート
    events.sort((a, b) => a.ratio - b.ratio);

    // 最初（0%）の表情を決定
    let initialExp = 'idle';
    // 最初のキーワードが10%以内にある場合は最初からその表情にする
    if (events.length > 0 && events[0].ratio < 0.1) {
        initialExp = events[0].exp;
    }

    // 0%地点のデフォルトイベントを追加
    if (events.length === 0 || events[0].ratio >= 0.1) {
        events.unshift({ ratio: 0, exp: initialExp, kw: 'default' });
    }

    return events;
}

// 外部（Svelte等）から呼び出せるようにグローバルに登録
window.deiningAvatarCore = window.deiningAvatarCore || {};
window.deiningAvatarCore.getMouthOpenValue = getMouthOpenValue;
window.deiningAvatarCore.isTtsPlaying = () => {
    return isSpeaking || isProcessingTts || (ttsQueue && ttsQueue.length > 0);
};
if (window.deiningAvatarCore.isModelVisible === undefined) {
    window.deiningAvatarCore.isModelVisible = true;
}
window.deiningAvatarCore.setModelVisibility = (visible) => {
    window.deiningAvatarCore.isModelVisible = visible;
    const canvasEl = document.getElementById('live2d-canvas');
    if (canvasEl) {
        if (visible) {
            canvasEl.style.display = 'block';
        } else {
            canvasEl.style.display = 'none';
            if (sysAvatarCtx && sysAvatarCtx.internalModel && sysAvatarCtx.internalModel.focusController) {
                sysAvatarCtx.internalModel.focusController.focus(0, 0);
            }
        }
    }
};

Object.assign(window.deiningAvatarCore, {
    mount: mountAvatarSystem,
    mountAvatarSystem: mountAvatarSystem,
    applyExpression: applyAvatarExpression,
    changeCostume: () => {
        rotateAvatarOutfit();
        if (sysAvatarCtx) {
            injectAvatarMods(sysAvatarCtx);
        }
    }
});

// 念のため DOMContentLoaded でもマウントを試みる
window.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('live2d-canvas')) {
        mountAvatarSystem();
    }
});// --- Hitbox Dynamic Styles ---
(function () {
    if (document.getElementById('hitbox-dynamic-styles')) return;
    const style = document.createElement('style');
    style.id = 'hitbox-dynamic-styles';
    style.innerHTML =
        "#hitbox-list-container { color-scheme: dark; }\n" +
        "#hitbox-list-container .hitbox-item { background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 6px; padding: 8px; display: flex; flex-direction: column; gap: 8px; margin-bottom: 8px; }\n" +
        "#hitbox-list-container .hitbox-item-row { display: flex; align-items: center; gap: 8px; }\n" +
        "#hitbox-list-container input, #hitbox-list-container select { background: rgba(0, 0, 0, 0.5); color: #fff; border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 4px; padding: 4px 8px; font-size: 12px; }\n" +
        "#hitbox-list-container select option { background: #222; color: #fff; }\n" +
        "#hitbox-list-container button { background: rgba(255, 50, 50, 0.2); color: #ff6b6b; border: 1px solid rgba(255, 50, 50, 0.5); border-radius: 4px; padding: 4px 8px; font-size: 12px; cursor: pointer; }\n" +
        "#hitbox-list-container button:hover { background: rgba(255, 50, 50, 0.4); }\n";
    document.head.appendChild(style);
})();
