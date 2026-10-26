# -*- coding: utf-8 -*-
"""Pure-Python GLB/VRM metadata parser.

泥濘 (Deinei) VRM モデル対応のメタデータ抽出・判定モジュール。
外部ライブラリに一切依存せず Python 標準ライブラリのみで動作。

- GLB magic/version/length の厳格なバリデーション
- glTF JSON チャンクの安全な展開 (BIN チャンクは参照しない)
- VRM 0.x (extensions.VRM) / VRM 1.0 (extensions.VRMC_vrm) の判別とメタデータ正規化
- 偽装ファイル (非GLB、壊れたJSON、巨大チャンクなど) の確実な遮断
"""

from __future__ import annotations

import json
import struct
from typing import Any, Dict, List, Optional

GLB_MAGIC = 0x46546C67    # 'glTF'
CHUNK_JSON = 0x4E4F534A   # 'JSON'
CHUNK_BIN = 0x004E4942    # 'BIN'

MAX_JSON_CHUNK_BYTES = 32 * 1024 * 1024  # glTF JSON チャンクの上限 (32MB, 偽装・巨大データDoS防止)

# VRM 0.x presetName -> VRM 1.0 presetName (three-vrm-core の v0v1PresetNameMap と同一仕様)
VRM0_TO_VRM1_PRESET = {
    'a': 'aa',
    'e': 'ee',
    'i': 'ih',
    'o': 'oh',
    'u': 'ou',
    'blink': 'blink',
    'blink_l': 'blinkLeft',
    'blink_r': 'blinkRight',
    'joy': 'happy',
    'angry': 'angry',
    'sorrow': 'sad',
    'fun': 'relaxed',
    'lookup': 'lookUp',
    'lookdown': 'lookDown',
    'lookleft': 'lookLeft',
    'lookright': 'lookRight',
    'neutral': 'neutral',
}


class VrmMetaError(ValueError):
    """GLB/VRM 判定・解析失敗時の例外。メッセージは API エラー応答として安全に返却可能。"""


def parse_glb_header(data: bytes) -> Dict[str, Any]:
    """12バイトの GLB ヘッダー + チャンク構造を検証し、実データ長との整合性を確認する。"""
    if len(data) < 12:
        raise VrmMetaError("VRM ファイルではありません (GLB ヘッダーサイズ不足)。")
    magic, version, total = struct.unpack_from('<III', data, 0)
    if magic != GLB_MAGIC:
        raise VrmMetaError("VRM ファイルではありません (glTF-Binary 形式ではありません)。")
    if version != 2:
        raise VrmMetaError(f"未対応の glTF バージョンです ({version})。glTF 2.0 のみ対応しています。")
    if len(data) != total:
        raise VrmMetaError("VRM ファイルが破損しています (ヘッダーの全体長と実ファイルサイズが不一致です)。")

    chunks: List[Dict[str, Any]] = []
    off = 12
    while off + 8 <= len(data):
        length, ctype = struct.unpack_from('<II', data, off)
        if length > len(data) - (off + 8):
            raise VrmMetaError("VRM ファイルが破損しています (チャンク長が実データ長を超過しています)。")
        chunks.append({'type': ctype, 'length': length, 'offset': off + 8})
        off += 8 + length
    return {'version': version, 'total': total, 'chunks': chunks}


def extract_gltf_json(data: bytes) -> Dict[str, Any]:
    """glTF JSON チャンクのみを安全に抽出してパースする。"""
    header = parse_glb_header(data)
    json_chunk = next((c for c in header['chunks'] if c['type'] == CHUNK_JSON), None)
    if json_chunk is None:
        raise VrmMetaError("VRM メタデータが見つかりません (glTF JSON チャンクがありません)。")
    if json_chunk['length'] > MAX_JSON_CHUNK_BYTES:
        raise VrmMetaError("VRM メタデータのサイズが上限 (32MB) を超過しています。")
    raw = data[json_chunk['offset']:json_chunk['offset'] + json_chunk['length']]
    try:
        gljson = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise VrmMetaError("VRM メタデータの JSON 解析に失敗しました。") from e
    if not isinstance(gljson, dict):
        raise VrmMetaError("VRM メタデータの形式が無効です。")
    return gljson


def parse_vrm_meta(data: bytes) -> Dict[str, Any]:
    """VRM 0.x / VRM 1.0 のメタデータを抽出し、正規化辞書として返す。VRM 拡張がない場合は VrmMetaError。"""
    gljson = extract_gltf_json(data)
    asset = gljson.get('asset') or {}
    extensions = gljson.get('extensions') or {}

    vrm1 = extensions.get('VRMC_vrm')
    vrm0 = extensions.get('VRM')
    if not vrm1 and not vrm0:
        raise VrmMetaError("VRM 拡張が含まれていません (通常の 3D モデルであり VRM ではありません)。")

    out: Dict[str, Any] = {
        'format': 'vrm1' if vrm1 else 'vrm0',
        'generator': asset.get('generator'),
        'meshes': len(gljson.get('meshes') or []),
        'materials': len(gljson.get('materials') or []),
        'images': len(gljson.get('images') or []),
        'extensionsUsed': list(gljson.get('extensionsUsed') or []),
    }

    if vrm1:
        meta = vrm1.get('meta') or {}
        exps = vrm1.get('expressions') or {}
        preset = exps.get('preset') or {}
        custom = exps.get('custom') or {}
        spring = extensions.get('VRMC_springBone') or {}
        spring_joints = 0
        for sp in (spring.get('springs') or []):
            if isinstance(sp, dict):
                spring_joints += len(sp.get('joints') or [])
        spring_joints += len([j for j in (spring.get('joints') or [])])
        out.update({
            'specVersion': vrm1.get('specVersion'),
            'name': meta.get('name') or '名称未設定',
            'authors': list(meta.get('authors') or []),
            'licenseUrl': meta.get('licenseUrl') or '',
            'humanBones': sorted((vrm1.get('humanoid') or {}).get('humanBones') or {}),
            'expressionPresets': sorted(preset.keys()),
            'expressionCustom': sorted(custom.keys()),
            'springJoints': spring_joints,
            'springColliders': len(spring.get('colliders') or []),
            'springSprings': len(spring.get('springs') or []),
        })
    else:
        meta = vrm0.get('meta') or {}
        groups = ((vrm0.get('blendShapeMaster') or {}).get('blendShapeGroups')) or []
        presets: List[str] = []
        binds = 0
        for g in groups:
            pn = (g.get('presetName') or '').lower()
            presets.append(VRM0_TO_VRM1_PRESET.get(pn, pn or (g.get('name') or '')))
            binds += len(g.get('binds') or [])
        humanoid = (vrm0.get('humanoid') or {}).get('humanBones') or []
        out.update({
            'name': meta.get('title') or '名称未設定',
            'authors': [meta.get('author')] if meta.get('author') else [],
            'licenseName': meta.get('licenseName') or '',
            'humanBones': sorted({b.get('bone') for b in humanoid if isinstance(b, dict) and b.get('bone')}),
            'expressionPresets': sorted({p for p in presets if p}),
            'expressionBindCount': binds,
        })
    return out


def looks_like_vrm(path: str, max_read_bytes: int = 64 * 1024 * 1024) -> Optional[Dict[str, Any]]:
    """ファイルヘッダー＋メタデータ部分のみを高速判定し、有効な VRM ならメタ辞書を、そうでなければ None を返す (例外を送出しない)。"""
    try:
        with open(path, 'rb') as f:
            data = f.read(max_read_bytes)
        if len(data) < 12 or struct.unpack_from('<I', data, 0)[0] != GLB_MAGIC:
            return None
        return parse_vrm_meta(data)
    except VrmMetaError:
        return None
    except OSError:
        return None
