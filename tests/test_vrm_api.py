# -*- coding: utf-8 -*-
"""VRM サーバー API (Phase 1) 単体テスト。

- GET /api/avatar/vrm/list
- POST /api/avatar/vrm/upload
- DELETE /api/avatar/vrm/<model_id>
- 60MB 上限・JSON 413 応答・重複ハッシュ判定・パストラバーサル防止・既存 Live2D 互換性
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# backend/data および tests をインポートパスに追加
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / 'backend' / 'data'
TESTS_DIR = REPO_ROOT / 'tests'
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

# テスト用の一時ディレクトリを環境変数に注入して本番環境と完全隔離
_TEST_TEMP_DIR = tempfile.mkdtemp(prefix='deinei_vrm_test_')
_TEST_CUSTOM_PUBLIC = os.path.join(_TEST_TEMP_DIR, 'custom_public')
_TEST_STATIC_DIR = os.path.join(_TEST_TEMP_DIR, 'static')
_TEST_CHATS_DIR = os.path.join(_TEST_TEMP_DIR, 'chats')
os.makedirs(_TEST_CUSTOM_PUBLIC, exist_ok=True)
os.makedirs(_TEST_STATIC_DIR, exist_ok=True)
os.makedirs(_TEST_CHATS_DIR, exist_ok=True)

os.environ['DEINEI_CUSTOM_PUBLIC_DIR'] = _TEST_CUSTOM_PUBLIC
os.environ['DEINEI_STATIC_DIR'] = _TEST_STATIC_DIR
os.environ['DEINEI_CHATS_DIR'] = _TEST_CHATS_DIR
os.environ['DEINEI_TEST_MODE'] = '1'

import server
from test_vrm_meta import make_vrm1_glb, make_vrm0_glb


class TestVrmApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.app.config['TESTING'] = True
        cls.client = server.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TEST_TEMP_DIR, ignore_errors=True)

    def setUp(self):
        # 各テスト前に VRM ディレクトリをクリーンアップ
        vrm_dir = os.path.join(_TEST_CUSTOM_PUBLIC, 'deining', 'vrm')
        if os.path.exists(vrm_dir):
            shutil.rmtree(vrm_dir, ignore_errors=True)
        static_vrm = os.path.join(_TEST_STATIC_DIR, 'vrm')
        if os.path.exists(static_vrm):
            shutil.rmtree(static_vrm, ignore_errors=True)

    def test_01_vrm_list_initially_empty(self):
        resp = self.client.get('/api/avatar/vrm/list')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('models', data)
        self.assertEqual(data['models'], [])

    def test_02_vrm_upload_success(self):
        vrm_bytes = make_vrm1_glb('AliciaSolid', ['Dwango'])
        data = {
            'file': (io.BytesIO(vrm_bytes), 'alicia.vrm')
        }
        resp = self.client.post('/api/avatar/vrm/upload', data=data, content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 200)
        res_json = resp.get_json()
        self.assertTrue(res_json.get('ok'))
        self.assertFalse(res_json.get('duplicate'))
        model = res_json.get('model')
        self.assertEqual(model['name'], 'AliciaSolid')
        self.assertEqual(model['kind'], 'vrm')
        self.assertEqual(model['format'], 'vrm1')
        self.assertIn('happy', model['expressions'])
        self.assertEqual(model['fileSize'], len(vrm_bytes))
        self.assertTrue(model['url'].startswith('/deining/vrm/'))

        # list で取得できること
        list_resp = self.client.get('/api/avatar/vrm/list')
        self.assertEqual(list_resp.status_code, 200)
        models = list_resp.get_json()['models']
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]['id'], model['id'])

    def test_03_vrm_upload_duplicate_detection(self):
        vrm_bytes = make_vrm1_glb('TwinModel', ['Author'])
        data1 = {'file': (io.BytesIO(vrm_bytes), 'twin1.vrm')}
        resp1 = self.client.post('/api/avatar/vrm/upload', data=data1, content_type='multipart/form-data')
        self.assertEqual(resp1.status_code, 200)
        self.assertFalse(resp1.get_json().get('duplicate'))
        first_id = resp1.get_json()['model']['id']

        # 同一内容を別のファイル名で再アップロード
        data2 = {'file': (io.BytesIO(vrm_bytes), 'twin2.vrm')}
        resp2 = self.client.post('/api/avatar/vrm/upload', data=data2, content_type='multipart/form-data')
        self.assertEqual(resp2.status_code, 200)
        res_json2 = resp2.get_json()
        self.assertTrue(res_json2.get('duplicate'))
        self.assertEqual(res_json2['model']['id'], first_id)

        # カタログ上の件数は 1 件のまま
        list_resp = self.client.get('/api/avatar/vrm/list')
        self.assertEqual(len(list_resp.get_json()['models']), 1)

    def test_04_vrm_upload_invalid_extension(self):
        data = {'file': (io.BytesIO(b'not a vrm'), 'malicious.exe')}
        resp = self.client.post('/api/avatar/vrm/upload', data=data, content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('拡張子', resp.get_json().get('error', ''))

    def test_05_vrm_upload_invalid_vrm_content(self):
        data = {'file': (io.BytesIO(b'THIS_IS_CORRUPT_DATA_1234567890'), 'broken.vrm')}
        resp = self.client.post('/api/avatar/vrm/upload', data=data, content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('無効なVRMモデル', resp.get_json().get('error', ''))

    def test_06_vrm_upload_size_limit_rejection(self):
        # 60MB 超過の Content-Length をシミュレート
        fake_huge_len = 65 * 1024 * 1024
        resp = self.client.post('/api/avatar/vrm/upload', environ_base={'CONTENT_LENGTH': str(fake_huge_len)})
        self.assertEqual(resp.status_code, 413)
        self.assertIn('ファイルサイズが上限(60MB)を超過', resp.get_json().get('error', ''))

    def test_07_vrm_delete_success(self):
        vrm_bytes = make_vrm0_glb('DeletableModel')
        data = {'file': (io.BytesIO(vrm_bytes), 'del.vrm')}
        resp = self.client.post('/api/avatar/vrm/upload', data=data, content_type='multipart/form-data')
        model_id = resp.get_json()['model']['id']

        # 削除実行
        del_resp = self.client.delete(f'/api/avatar/vrm/{model_id}')
        self.assertEqual(del_resp.status_code, 200)
        self.assertTrue(del_resp.get_json().get('ok'))
        self.assertEqual(del_resp.get_json().get('deleted'), model_id)

        # カタログから消えていること
        list_resp = self.client.get('/api/avatar/vrm/list')
        self.assertEqual(len(list_resp.get_json()['models']), 0)

    def test_08_vrm_delete_nonexistent(self):
        resp = self.client.delete('/api/avatar/vrm/nonexistent_model_id')
        self.assertEqual(resp.status_code, 404)

    def test_09_vrm_delete_invalid_id_traversal(self):
        resp = self.client.delete('/api/avatar/vrm/.._bad_id')
        self.assertEqual(resp.status_code, 400)

    def test_10_live2d_endpoint_remains_untouched(self):
        # 既存の Live2D エンドポイント /api/avatar/models が影響を受けていないこと
        resp = self.client.get('/api/avatar/models')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('models', data)
        ids = [m['id'] for m in data['models']]
        self.assertIn('silver', ids)
        self.assertIn('brown', ids)


if __name__ == '__main__':
    unittest.main()
