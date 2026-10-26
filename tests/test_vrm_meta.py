# -*- coding: utf-8 -*-
"""VRM メタデータパーサー (vrm_meta.py) 単体テスト。

リポジトリ内に巨大なバイナリ VRM ファイルを保持せず、
テストコード内で規格準拠の最小 GLB/VRM バイト列を動的合成して網羅検証する。
"""

import json
import os
import struct
import sys
import unittest
from pathlib import Path

# backend/data をインポートパスに追加
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / 'backend' / 'data'
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from vrm_meta import (
    CHUNK_BIN,
    CHUNK_JSON,
    GLB_MAGIC,
    VrmMetaError,
    extract_gltf_json,
    parse_glb_header,
    parse_vrm_meta,
    looks_like_vrm,
)


def make_glb(gljson: dict, bin_data: bytes = b'') -> bytes:
    """glTF JSON 構造体から有効な glTF 2.0 Binary (GLB) バイト列を生成する"""
    raw_json = json.dumps(gljson).encode('utf-8')
    json_pad = (-len(raw_json)) % 4
    raw_json += b' ' * json_pad
    chunks = struct.pack('<II', len(raw_json), CHUNK_JSON) + raw_json

    if bin_data:
        bin_pad = (-len(bin_data)) % 4
        padded_bin = bin_data + (b'\x00' * bin_pad)
        chunks += struct.pack('<II', len(padded_bin), CHUNK_BIN) + padded_bin

    total_len = 12 + len(chunks)
    return struct.pack('<III', GLB_MAGIC, 2, total_len) + chunks


def make_vrm1_glb(name='TestVRM1', authors=None) -> bytes:
    """VRM 1.0 拡張を含む最小 GLB を生成"""
    return make_glb({
        'asset': {'version': '2.0', 'generator': 'Antigravity Test'},
        'extensions': {
            'VRMC_vrm': {
                'specVersion': '1.0',
                'meta': {
                    'name': name,
                    'authors': authors or ['Author A', 'Author B'],
                    'licenseUrl': 'https://example.com/license'
                },
                'humanoid': {
                    'humanBones': {
                        'head': {'node': 1},
                        'hips': {'node': 0},
                        'spine': {'node': 2}
                    }
                },
                'expressions': {
                    'preset': {
                        'happy': {'morphTargetBinds': []},
                        'angry': {'morphTargetBinds': []},
                        'aa': {'morphTargetBinds': []},
                        'blink': {'morphTargetBinds': []}
                    },
                    'custom': {
                        'smug': {'morphTargetBinds': []}
                    }
                }
            },
            'VRMC_springBone': {
                'springs': [{'joints': [{}, {}]}],
                'colliders': [{}]
            }
        },
        'meshes': [{}],
        'materials': [{}]
    })


def make_vrm0_glb(title='TestVRM0', author='Author0') -> bytes:
    """VRM 0.x 拡張を含む最小 GLB を生成"""
    return make_glb({
        'asset': {'version': '2.0', 'generator': 'UniVRM Test'},
        'extensions': {
            'VRM': {
                'meta': {
                    'title': title,
                    'author': author,
                    'licenseName': 'CC_BY'
                },
                'humanoid': {
                    'humanBones': [
                        {'bone': 'hips', 'node': 0},
                        {'bone': 'head', 'node': 1}
                    ]
                },
                'blendShapeMaster': {
                    'blendShapeGroups': [
                        {'presetName': 'joy', 'binds': [{}]},
                        {'presetName': 'a', 'binds': [{}]},
                        {'presetName': 'blink', 'binds': [{}]},
                        {'presetName': 'fun', 'binds': [{}]}
                    ]
                }
            }
        },
        'meshes': [{}]
    })


class TestVrmMetaSynthetic(unittest.TestCase):
    def test_vrm1_synthetic(self):
        blob = make_vrm1_glb('MyVRM1Model', ['Developer'])
        meta = parse_vrm_meta(blob)
        self.assertEqual(meta['format'], 'vrm1')
        self.assertEqual(meta['name'], 'MyVRM1Model')
        self.assertEqual(meta['authors'], ['Developer'])
        self.assertEqual(meta['licenseUrl'], 'https://example.com/license')
        self.assertIn('happy', meta['expressionPresets'])
        self.assertIn('aa', meta['expressionPresets'])
        self.assertIn('smug', meta['expressionCustom'])
        self.assertIn('head', meta['humanBones'])
        self.assertEqual(meta['springJoints'], 2)
        self.assertEqual(meta['springColliders'], 1)

    def test_vrm0_synthetic(self):
        blob = make_vrm0_glb('MyVRM0Model', 'Developer0')
        meta = parse_vrm_meta(blob)
        self.assertEqual(meta['format'], 'vrm0')
        self.assertEqual(meta['name'], 'MyVRM0Model')
        self.assertEqual(meta['authors'], ['Developer0'])
        self.assertEqual(meta['licenseName'], 'CC_BY')
        # VRM 0.x -> 1.0 のプリセット名正規化
        self.assertIn('happy', meta['expressionPresets'])   # joy -> happy
        self.assertIn('aa', meta['expressionPresets'])      # a -> aa
        self.assertIn('relaxed', meta['expressionPresets']) # fun -> relaxed
        self.assertIn('head', meta['humanBones'])
        self.assertGreater(meta['expressionBindCount'], 0)


class TestVrmMetaRejection(unittest.TestCase):
    def test_reject_non_glb(self):
        with self.assertRaises(VrmMetaError) as cm:
            parse_vrm_meta(b'PK\x03\x04' + b'\x00' * 32)
        self.assertIn("glTF-Binary", str(cm.exception))

    def test_reject_short_data(self):
        with self.assertRaises(VrmMetaError):
            parse_vrm_meta(b'short')

    def test_reject_bad_gltf_version(self):
        body = make_glb({'asset': {'version': '2.0'}})
        # version を 1 に改ざん
        corrupted = struct.pack('<III', GLB_MAGIC, 1, len(body)) + body[12:]
        with self.assertRaises(VrmMetaError) as cm:
            parse_vrm_meta(corrupted)
        self.assertIn("glTF 2.0", str(cm.exception))

    def test_reject_length_mismatch(self):
        body = make_glb({'asset': {'version': '2.0'}})
        # 全体長を 10 バイト偽装
        corrupted = struct.pack('<III', GLB_MAGIC, 2, len(body) + 10) + body[12:]
        with self.assertRaises(VrmMetaError) as cm:
            parse_vrm_meta(corrupted)
        self.assertIn("ファイルサイズが不一致", str(cm.exception))

    def test_reject_plain_glb_without_vrm_extension(self):
        # 通常の 3D モデル (VRM 拡張がない GLB)
        plain_glb = make_glb({'asset': {'version': '2.0'}, 'meshes': [{}]})
        with self.assertRaises(VrmMetaError) as cm:
            parse_vrm_meta(plain_glb)
        self.assertIn("VRM 拡張が含まれていません", str(cm.exception))

    def test_reject_glb_missing_json_chunk(self):
        # BIN チャンクのみで JSON チャンクがない
        bin_chunk = struct.pack('<II', 4, CHUNK_BIN) + b'\x00\x00\x00\x00'
        corrupted = struct.pack('<III', GLB_MAGIC, 2, 12 + len(bin_chunk)) + bin_chunk
        with self.assertRaises(VrmMetaError) as cm:
            parse_vrm_meta(corrupted)
        self.assertIn("glTF JSON チャンクがありません", str(cm.exception))

    def test_reject_broken_json(self):
        raw = b'{ "broken json ... '
        chunk = struct.pack('<II', len(raw), CHUNK_JSON) + raw
        corrupted = struct.pack('<III', GLB_MAGIC, 2, 12 + len(chunk)) + chunk
        with self.assertRaises(VrmMetaError) as cm:
            parse_vrm_meta(corrupted)
        self.assertIn("JSON 解析に失敗しました", str(cm.exception))


class TestLooksLikeVrm(unittest.TestCase):
    def test_looks_like_vrm_with_valid_model(self):
        import tempfile
        blob = make_vrm1_glb('SafeTest')
        with tempfile.NamedTemporaryFile(suffix='.vrm', delete=False) as f:
            f.write(blob)
            path = f.name
        try:
            res = looks_like_vrm(path)
            self.assertIsNotNone(res)
            self.assertEqual(res['name'], 'SafeTest')
        finally:
            os.remove(path)

    def test_looks_like_vrm_with_invalid_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.vrm', delete=False) as f:
            f.write(b'THIS_IS_NOT_A_VRM_FILE')
            path = f.name
        try:
            self.assertIsNone(looks_like_vrm(path))
        finally:
            os.remove(path)


if __name__ == '__main__':
    unittest.main()
