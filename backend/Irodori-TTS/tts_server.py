from fastapi import FastAPI, Response, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import io
import soundfile as sf
import numpy as np
import re
import os

from irodori_tts.inference_runtime import (
    RuntimeKey,
    SamplingRequest,
    get_cached_runtime,
    clear_cached_runtime,
    default_runtime_device,
)

app = FastAPI()

# ====== セキュリティ設定: 許可オリジン・ホストの厳格管理 ======
ALLOWED_ORIGINS = {
    'http://localhost:8080',
    'http://127.0.0.1:8080',
    'http://localhost:5000',
    'http://127.0.0.1:5000',
    'http://localhost:5001',
    'http://127.0.0.1:5001',
}

ALLOWED_HOSTS = {
    'localhost:5001',
    '127.0.0.1:5001',
    'localhost',
    '127.0.0.1',
}

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # 1. DNS Rebinding 対策: Host ヘッダー検証
    host = request.headers.get("host", "").lower()
    if host and host not in ALLOWED_HOSTS:
        return JSONResponse(status_code=400, content={"error": "Invalid Host header"})

    # 2. CSRF 対策: 状態変更メソッド (非GET/HEAD/OPTIONS) に対する Origin / Referer 検査
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")

        # Origin: null は無条件で拒否
        if origin and origin.lower() in ("null", "none"):
            return JSONResponse(status_code=403, content={"error": "Cross-origin request forbidden (null origin)"})

        if origin:
            if origin.rstrip("/") not in ALLOWED_ORIGINS:
                return JSONResponse(status_code=403, content={"error": "Cross-origin request forbidden"})
        elif referer:
            from urllib.parse import urlparse
            ref_parsed = urlparse(referer)
            ref_origin = f"{ref_parsed.scheme}://{ref_parsed.netloc}"
            if ref_origin not in ALLOWED_ORIGINS:
                return JSONResponse(status_code=403, content={"error": "Cross-origin request forbidden (invalid referer)"})
        else:
            sec_fetch_site = request.headers.get("sec-fetch-site")
            if sec_fetch_site and sec_fetch_site not in ("same-origin", "none"):
                return JSONResponse(status_code=403, content={"error": "Cross-site request forbidden"})

    if request.method == "OPTIONS" and request.url.path in ("/", "/health"):
        return Response(status_code=204, headers={
            "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With"
        })

    response = await call_next(request)
    # ヘルスチェック (安全な読み取り) の場合はローカルスプラッシュ画面 (file:/// 等) からの取得を常に許可
    if request.url.path in ("/", "/health") and request.method in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        response.headers["Access-Control-Allow-Origin"] = origin if origin else "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

@app.get("/")
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Irodori-TTS"}

class TTSRequest(BaseModel):
    text: str = "こんにちは、図書室のご利用ですね。勉強机の使用でしたら右にどうぞ。"
    caption: str = ""
    speed: float = 1.0
    seed: int | None = 100
    split_chunks: bool = True

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL_PATHS = [
    os.path.join(_SCRIPT_DIR, "models", "model.safetensors"),
    os.path.join(_SCRIPT_DIR, "model.safetensors")
]

def _get_checkpoint_path():
    for p in DEFAULT_MODEL_PATHS:
        if os.path.exists(p):
            print(f"ローカルモデルを読み込みます: {p}")
            return p
    checkpoint_repo = "Aratako/Irodori-TTS-v4-Small-Quantized"
    from huggingface_hub import hf_hub_download
    try:
        return hf_hub_download(repo_id=checkpoint_repo, filename="float8-weight-only/model.safetensors")
    except Exception:
        return hf_hub_download(repo_id=checkpoint_repo, filename="int8-weight-only/model.safetensors")

def _build_runtime_key_voicedesign():
    checkpoint_path = _get_checkpoint_path()
    model_device = default_runtime_device()
    return RuntimeKey(
        checkpoint=checkpoint_path,
        model_device=model_device,
        codec_repo="Aratako/Semantic-DACVAE-Japanese-32dim",
        model_precision="bf16",
        codec_device=model_device,
        codec_precision="fp32",
        compile_model=False,
        compile_dynamic=False,
    )

def _build_runtime_key_base():
    checkpoint_path = _get_checkpoint_path()
    model_device = default_runtime_device()
    return RuntimeKey(
        checkpoint=checkpoint_path,
        model_device=model_device,
        codec_repo="Aratako/Semantic-DACVAE-Japanese-32dim",
        model_precision="bf16",  # INT8量子化モデルの計算精度はbf16を指定
        codec_device=model_device,
        codec_precision="fp32",
        compile_model=False,
        compile_dynamic=False,
    )

REFERENCE_WAV_PATH = os.path.join(_SCRIPT_DIR, "reference_voice.wav")

@app.get("/")
def read_root():
    return {"status": "running", "message": "Irodori-TTS Server is active"}

@app.on_event("startup")
def load_model():
    print("Irodori-TTSサーバーの初期化を開始します...")
    try:
        if not os.path.exists(REFERENCE_WAV_PATH):
            print("初期リファレンス音声が見つかりません。VoiceDesignモデルで生成します...")
            key_design = _build_runtime_key_voicedesign()
            runtime_design, _ = get_cached_runtime(key_design)
            
            # 基準となるサンプルテキストと固定の指示
            sample_text = "おはようございます。お兄さん、これからよろしくお願いします。"
            caption = "普通のトーンで、ダウナー系のロリボイス。"
            seed = None  # Noneにすることで毎回ランダムな声（ガチャ）になるように変更
            
            result = runtime_design.synthesize(
                SamplingRequest(
                    text=sample_text,
                    caption=caption,
                    ref_wav=None,
                    ref_latent=None,
                    no_ref=True,
                    ref_normalize_db=-16.0,
                    ref_ensure_max=True,
                    num_candidates=1,
                    decode_mode="sequential",
                    seconds=30.0,
                    max_ref_seconds=30.0,
                    max_text_len=256,
                    max_caption_len=256,
                    num_steps=40,
                    seed=seed,
                    cfg_guidance_mode="independent",
                    cfg_scale_text=2.0,
                    cfg_scale_caption=4.0,
                    cfg_scale_speaker=0.0,
                    cfg_scale=None,
                    cfg_min_t=0.5,
                    cfg_max_t=1.0,
                    truncation_factor=None,
                    rescale_k=None,
                    rescale_sigma=None,
                    context_kv_cache=True,
                    speaker_kv_scale=None,
                    speaker_kv_min_t=None,
                    speaker_kv_max_layers=None,
                    trim_tail=True,
                ),
                log_fn=lambda x: None,
            )
            audio_tensor = result.audios[0]
            sample_rate = result.sample_rate
            audio_array = audio_tensor.cpu().numpy()
            if audio_array.ndim == 2:
                audio_array = audio_array.T
            sf.write(REFERENCE_WAV_PATH, audio_array, sample_rate, format='WAV', subtype='PCM_16')
            print(f"リファレンス音声を {REFERENCE_WAV_PATH} に保存しました。")
            
            # VoiceDesignモデルをアンロード
            clear_cached_runtime()
            print("VoiceDesignモデルをメモリから解放しました。")
        else:
            print(f"既存のリファレンス音声 ({REFERENCE_WAV_PATH}) を使用します。")
        
        print("Baseモデル（VoiceClone用）をロードしています...")
        key_base = _build_runtime_key_base()
        get_cached_runtime(key_base)
        print("Baseモデルのロードが完了しました。")
    except Exception as e:
        print(f"❌ モデルのロード中にエラーが発生しました: {e}")
        print("VRAM不足の可能性があります。Ollamaのモデルを一度終了してから再試行してください。")
def clean_text(text: str) -> str:
    # 1. 思考タグ
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # 2. コードブロック
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # 3. 見出しやリスト記号
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # 4. 太字・斜体（ト書き除去前に展開）
    text = re.sub(r'\*\*\*([^*]+)\*\*\*', r'\1', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'___([^_]+)___', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    # 5. リンク
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # 6. ト書き（アスタリスクで囲まれた動作描写、改行跨ぎ対応）
    text = re.sub(r'＊+([\s\S]*?)＊+', '', text)
    text = re.sub(r'\*+([\s\S]*?)\*+', '', text)
    # 7. 単独行のカッコト書き（文中の「（小説）」等の補足は保護）
    text = re.sub(r'^[（(][^\n]+[）)]$', '', text, flags=re.MULTILINE)
    # 8. カギ括弧（「」『』“”"）の外枠を削除して中身の文字列のみ残す
    text = re.sub(r'[「」『』“”"]', '', text)
    # 9. 不要な記号 & 孤立アスタリスクの完全除去 (TTS誤読防止)
    text = re.sub(r'^[>|]\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'[*＊]', '', text)
    return text.strip()

def split_sentences(text: str) -> list[str]:
    sentences = re.split(r'(?<=[。！？!?\n])', text)
    return [s.strip() for s in sentences if s.strip()]

@app.post("/tts")
def generate_tts(req: TTSRequest):
    print(f"\n==========================================")
    print(f"🔊 [TTS Server] Request received: split_chunks={req.split_chunks}, speed={req.speed}")
    print(f"テキスト（生, {len(req.text)}文字）: {req.text}")
    clean_str = clean_text(req.text)
    print(f"クリーニング後 ({len(clean_str)}文字): {clean_str}")
    print(f"==========================================")
    
    if not clean_str:
        buf = io.BytesIO()
        sf.write(buf, np.zeros((0,), dtype=np.float32), 24000, format='WAV', subtype='PCM_16')
        buf.seek(0)
        return Response(content=buf.read(), media_type="audio/wav")
        
    # 100文字程度を一つの塊として、文を統合して処理する (split_chunksがTrueの場合)
    if req.split_chunks:
        raw_sentences = split_sentences(clean_str)
        sentences = []
        current_chunk = ""
        
        for s in raw_sentences:
            if len(current_chunk) + len(s) > 100 and current_chunk:
                sentences.append(current_chunk)
                current_chunk = s
            else:
                current_chunk += s
        if current_chunk:
            sentences.append(current_chunk)
        print(f"100文字目安で {len(sentences)} つのブロックにまとめて生成します。")
    else:
        sentences = [clean_str]
        print("ブロック分割オフ: 全文章を一度に生成します。")
    
    key_base = _build_runtime_key_base()
    runtime, _ = get_cached_runtime(key_base)
    
    audio_arrays = []
    sample_rate = 24000
    
    duration_scale = 1.0 / max(0.1, req.speed)
    
    for sentence in sentences:
        if not sentence: continue
        
        result = runtime.synthesize(
            SamplingRequest(
                text=sentence,
                caption=None,
                ref_wav=REFERENCE_WAV_PATH,
                ref_latent=None,
                no_ref=False,
                ref_normalize_db=-16.0,
                ref_ensure_max=True,
                num_candidates=1,
                decode_mode="sequential",
                seconds=None, # v3の自動長さ予測機能(Duration Predictor)を使用する
                duration_scale=duration_scale,
                max_ref_seconds=30.0,
                max_text_len=512, # 許容文字数を拡張
                max_caption_len=256,
                num_steps=40,
                seed=req.seed,
                cfg_guidance_mode="independent",
                cfg_scale_text=3.0,
                cfg_scale_caption=0.0,
                cfg_scale_speaker=5.0,
                context_kv_cache=True,
                trim_tail=True,
            ),
            log_fn=lambda x: None,
        )
        
        audio_tensor = result.audios[0]
        sample_rate = result.sample_rate
        
        audio_array = audio_tensor.cpu().numpy()
        if audio_array.ndim == 2:
            audio_array = audio_array.T
            
        audio_arrays.append(audio_array)
        
        # 分割されている場合のみ、文の間に短いポーズ（0.15秒）を挿入
        if len(sentences) > 1:
            pause = np.zeros((int(sample_rate * 0.15),) + audio_array.shape[1:], dtype=audio_array.dtype)
            audio_arrays.append(pause)
            
    if not audio_arrays:
        buf = io.BytesIO()
        sf.write(buf, np.zeros((0,), dtype=np.float32), 24000, format='WAV', subtype='PCM_16')
        buf.seek(0)
        return Response(content=buf.read(), media_type="audio/wav")
        
    final_audio = np.concatenate(audio_arrays, axis=0)
    
    buf = io.BytesIO()
    sf.write(buf, final_audio, sample_rate, format='WAV', subtype='PCM_16')
    buf.seek(0)
    
    return Response(content=buf.read(), media_type="audio/wav")
        
    if not audio_arrays:
        buf = io.BytesIO()
        sf.write(buf, np.zeros((0,), dtype=np.float32), 24000, format='WAV', subtype='PCM_16')
        buf.seek(0)
        return Response(content=buf.read(), media_type="audio/wav")
        
    final_audio = np.concatenate(audio_arrays, axis=0)
    
    buf = io.BytesIO()
    sf.write(buf, final_audio, sample_rate, format='WAV', subtype='PCM_16')
    buf.seek(0)
    
    return Response(content=buf.read(), media_type="audio/wav")

if __name__ == "__main__":
    print("Irodori-TTS サーバーを起動します: http://127.0.0.1:5001")
    uvicorn.run("tts_server:app", host="127.0.0.1", port=5001, reload=True)
