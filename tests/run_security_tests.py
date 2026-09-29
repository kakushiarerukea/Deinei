import os, sys, io, json, zipfile, tempfile, shutil
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import requests

SERVER_URL = "http://127.0.0.1:5000"
TTS_URL = "http://127.0.0.1:5001"

def test_session_id_traversal():
    print("\n========================================================")
    print("🧪 1. session_id パストラバーサル全ルート検証 (GET, POST, DELETE)")
    print("========================================================")
    
    cases = [
        ("GET", "/api/sessions/../../evil", None),
        ("POST", "/api/sessions/../../evil", {"name": "test"}),
        ("DELETE", "/api/sessions/../../evil", None),
        ("GET", "/api/sessions/..\\..\\evil", None),
        ("POST", "/api/sessions/..\\..\\evil", {"name": "test"}),
        ("DELETE", "/api/sessions/..\\..\\evil", None),
        ("POST", "/api/sessions", {"id": "../../evil", "name": "test"}),
        ("POST", "/api/sessions", {"id": "..\\..\\evil", "name": "test"}),
    ]

    headers = {"Origin": "http://localhost:8080"}
    for method, path, body in cases:
        url = SERVER_URL + path
        if method == "GET":
            r = requests.get(url, headers=headers)
        elif method == "POST":
            r = requests.post(url, json=body, headers=headers)
        elif method == "DELETE":
            r = requests.delete(url, headers=headers)
        print(f"[{method}] {path} -> Status: {r.status_code}, Body: {r.text.strip()}")
        assert r.status_code in (400, 404), f"Failed to reject: {r.status_code}"

def test_origin_null_and_csrf():
    print("\n========================================================")
    print("🧪 2. Origin: null および CSRF 遮断テスト (5000: Data Server)")
    print("========================================================")
    
    # Origin: null (sandbox iframe や data: URI)
    r1 = requests.post(f"{SERVER_URL}/memory/clear", headers={"Origin": "null"})
    print(f"[POST /memory/clear] Origin: null -> Status: {r1.status_code}, Body: {r1.text.strip()}")
    assert r1.status_code == 403, f"Origin: null was not rejected: {r1.status_code}"

    # Origin: https://evil.example
    r2 = requests.post(f"{SERVER_URL}/memory/clear", headers={"Origin": "https://evil.example"})
    print(f"[POST /memory/clear] Origin: https://evil.example -> Status: {r2.status_code}, Body: {r2.text.strip()}")
    assert r2.status_code == 403, f"Evil origin was not rejected: {r2.status_code}"

    # Form POST 模倣 (Sec-Fetch-Site: cross-site)
    r3 = requests.post(f"{SERVER_URL}/memory/clear", headers={"Sec-Fetch-Site": "cross-site"})
    print(f"[POST /memory/clear] Sec-Fetch-Site: cross-site -> Status: {r3.status_code}, Body: {r3.text.strip()}")
    assert r3.status_code == 403, f"Cross-site request was not rejected: {r3.status_code}"

def test_tts_server_security():
    print("\n========================================================")
    print("🧪 3. 5001 (TTS Server) セキュリティ検証 (DNS Rebinding / CSRF)")
    print("========================================================")
    
    try:
        # 正常系チェック (起動していればテスト)
        r_health = requests.get(f"{TTS_URL}/health", timeout=1)
        if r_health.status_code != 200:
            print("  ℹ️ TTS サーバー未起動のためスキップ")
            return
    except Exception:
        print("  ℹ️ TTS サーバー未起動のためスキップ")
        return

    # Host ヘッダー偽装 (DNS Rebinding)
    r_host = requests.get(f"{TTS_URL}/health", headers={"Host": "evil.example"})
    print(f"[TTS /health] Host: evil.example -> Status: {r_host.status_code}")
    assert r_host.status_code == 400, f"TTS DNS rebinding not rejected: {r_host.status_code}"

    # Origin: null
    r_null = requests.post(f"{TTS_URL}/tts", json={"text": "test"}, headers={"Origin": "null"})
    print(f"[TTS /tts] Origin: null -> Status: {r_null.status_code}")
    assert r_null.status_code == 403, f"TTS null origin not rejected: {r_null.status_code}"

    # 悪意ある Origin
    r_evil = requests.post(f"{TTS_URL}/tts", json={"text": "test"}, headers={"Origin": "https://evil.example"})
    print(f"[TTS /tts] Origin: https://evil.example -> Status: {r_evil.status_code}")
    assert r_evil.status_code == 403, f"TTS evil origin not rejected: {r_evil.status_code}"

def test_avatar_upload_path_removal():
    print("\n========================================================")
    print("🧪 4. アバターアップロード: path 引数廃止のテスト")
    print("========================================================")
    
    headers = {"Origin": "http://localhost:8080"}
    # JSON body で path を指定
    r = requests.post(f"{SERVER_URL}/api/avatar/upload", json={"path": "C:\\Windows\\win.ini"}, headers=headers)
    print(f"[POST /api/avatar/upload (JSON path)] -> Status: {r.status_code}, Body: {r.text.strip()}")
    assert r.status_code == 400, f"Expected 400, got {r.status_code}"

    # multipart form-data で path のみを指定
    r2 = requests.post(f"{SERVER_URL}/api/avatar/upload", data={"path": "C:\\Windows\\win.ini"}, headers=headers)
    print(f"[POST /api/avatar/upload (Form path)] -> Status: {r2.status_code}, Body: {r2.text.strip()}")
    assert r2.status_code == 400, f"Expected 400, got {r2.status_code}"

def test_avatar_zip_security():
    print("\n========================================================")
    print("🧪 5. アバターZIPセキュリティ検証 (悪意ファイル除外/サイズ上限/ファイル数上限/ネスト計上)")
    print("========================================================")
    
    headers = {"Origin": "http://localhost:8080"}

    # A. 悪意あるスクリプト (.html / .js / .py) が混入したZIP
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
        evil_zip_path = tf.name
    with zipfile.ZipFile(evil_zip_path, 'w') as z:
        z.writestr("ulvm2_0001/ulvm2_0001.model3.json", json.dumps({"Version": 3, "FileReferences": {}}))
        z.writestr("ulvm2_0001/ulvm2_0001.moc3", b"dummy moc3 data")
        z.writestr("ulvm2_0001/texture_00.png", b"dummy png data")
        z.writestr("ulvm2_0001/malicious.html", b"<script>alert('xss')</script>")
        z.writestr("ulvm2_0001/evil.js", b"console.log('pwned')")
        z.writestr("ulvm2_0001/exploit.py", b"import os; os.system('calc')")
    
    try:
        with open(evil_zip_path, 'rb') as f:
            r = requests.post(f"{SERVER_URL}/api/avatar/upload", files={'file': ('evil.zip', f, 'application/zip')}, headers=headers)
        print(f"[A: 悪意スクリプト混入ZIP] -> Status: {r.status_code}, Body: {r.text.strip()}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        
        custom_public_dest = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend', 'llama.cpp-b9873', 'custom_public', 'deining', 'ulvm2_0001'))
        html_exists = os.path.exists(os.path.join(custom_public_dest, 'malicious.html'))
        js_exists = os.path.exists(os.path.join(custom_public_dest, 'evil.js'))
        py_exists = os.path.exists(os.path.join(custom_public_dest, 'exploit.py'))
        moc3_exists = os.path.exists(os.path.join(custom_public_dest, 'ulvm2_0001.moc3'))
        
        print(f"  - .html 遮断確認: {'✅ 排除成功 (配置されていません)' if not html_exists else '❌ 危険: 配置されました'}")
        print(f"  - .js 遮断確認:   {'✅ 排除成功 (配置されていません)' if not js_exists else '❌ 危険: 配置されました'}")
        print(f"  - .py 遮断確認:   {'✅ 排除成功 (配置されていません)' if not py_exists else '❌ 危険: 配置されました'}")
        print(f"  - .moc3 配置確認: {'✅ 正常配置' if moc3_exists else '❌ 欠落'}")
        assert not html_exists and not js_exists and not py_exists, "Malicious files were copied!"
    finally:
        if os.path.exists(evil_zip_path):
            os.remove(evil_zip_path)

    # B. 150MB 上限超過テスト
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
        large_zip_path = tf.name
    with zipfile.ZipFile(large_zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("ulvm2_0001/ulvm2_0001.model3.json", b"{}")
        z.writestr("ulvm2_0001/large_bomb.png", b"\x00" * (160 * 1024 * 1024))
    try:
        with open(large_zip_path, 'rb') as f:
            r = requests.post(f"{SERVER_URL}/api/avatar/upload", files={'file': ('bomb.zip', f, 'application/zip')}, headers=headers)
        print(f"[B: 150MB上限超過テスト] -> Status: {r.status_code}, Body: {r.text.strip()}")
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
    finally:
        if os.path.exists(large_zip_path):
            os.remove(large_zip_path)

    # C. 300ファイル上限超過テスト
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
        many_files_zip = tf.name
    with zipfile.ZipFile(many_files_zip, 'w') as z:
        z.writestr("ulvm2_0001/ulvm2_0001.model3.json", b"{}")
        for i in range(320):
            z.writestr(f"ulvm2_0001/dummy_{i}.png", b"x")
    try:
        with open(many_files_zip, 'rb') as f:
            r = requests.post(f"{SERVER_URL}/api/avatar/upload", files={'file': ('many.zip', f, 'application/zip')}, headers=headers)
        print(f"[C: 300ファイル上限超過テスト] -> Status: {r.status_code}, Body: {r.text.strip()}")
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
    finally:
        if os.path.exists(many_files_zip):
            os.remove(many_files_zip)

    # D. ネストZIP合算上限超過テスト
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf_inner:
        inner_zip_path = tf_inner.name
    with zipfile.ZipFile(inner_zip_path, 'w') as z_in:
        for i in range(295):
            z_in.writestr(f"inner_{i}.png", b"x")

    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf_outer:
        outer_zip_path = tf_outer.name
    with zipfile.ZipFile(outer_zip_path, 'w') as z_out:
        z_out.writestr("ulvm2_0001/ulvm2_0001.model3.json", b"{}")
        for i in range(15):
            z_out.writestr(f"outer_{i}.png", b"x")
        z_out.write(inner_zip_path, "ulvm2_0001/nested.zip")
    
    try:
        with open(outer_zip_path, 'rb') as f:
            r = requests.post(f"{SERVER_URL}/api/avatar/upload", files={'file': ('nested.zip', f, 'application/zip')}, headers=headers)
        print(f"[D: ネストZIP合算上限超過テスト] -> Status: {r.status_code}, Body: {r.text.strip()}")
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
    finally:
        for p in (inner_zip_path, outer_zip_path):
            if os.path.exists(p):
                os.remove(p)

def test_silver_model_actual_load():
    print("\n========================================================")
    print("🧪 6. 実データ「5衣装の銀髪モデル」のZIP化・読み込み検証")
    print("========================================================")
    
    silver_src = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend', 'data', 'ulvm2_0001'))
    assert os.path.isdir(silver_src), f"Silver model dir not found: {silver_src}"

    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
        actual_zip_path = tf.name
    
    file_count = 0
    total_raw_size = 0
    with zipfile.ZipFile(actual_zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(silver_src):
            for f in files:
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, silver_src)
                z.write(full_path, os.path.join("ulvm2_0001", rel_path))
                file_count += 1
                total_raw_size += os.path.getsize(full_path)
    
    print(f"  - 銀髪モデル実データ統計: ファイル数 = {file_count}個, 非圧縮サイズ = {total_raw_size / (1024*1024):.2f}MB")

    headers = {"Origin": "http://localhost:8080"}
    try:
        with open(actual_zip_path, 'rb') as f:
            r = requests.post(f"{SERVER_URL}/api/avatar/upload", files={'file': ('silver_actual.zip', f, 'application/zip')}, headers=headers)
        print(f"[実データアップロード結果] -> Status: {r.status_code}, Body: {r.text.strip()}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        
        dest_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend', 'llama.cpp-b9873', 'custom_public', 'deining', 'ulvm2_0001'))
        key_files = [
            'ulvm2_0001.model3.json',
            'ulvm2_0001.moc3',
            'icon_ulvm2_0001.jpg',
            'ulvm2_0001.cdi3.json',
            'ulvm2_0001.physics3.json',
            'ulvm2_0001.vtube.json',
            'items_pinned_to_model.json',
            'key.txt',
            'animation/costume_v0000.exp3.json',
            'ulvm2_0001.4096/texture_00.png',
        ]
        
        all_ok = all(os.path.exists(os.path.join(dest_dir, kf)) for kf in key_files)
        if all_ok:
            print("  ✅ 全アセット（5衣装・テクスチャ・JPGアイコン・VTube設定等）が欠落なく完全配置されました！")
        assert all_ok, "Some critical avatar assets are missing!"
    finally:
        if os.path.exists(actual_zip_path):
            os.remove(actual_zip_path)

def test_deduplicate_api():
    print("\n========================================================")
    print("🧪 7. 記憶重複統合 API (/memory/deduplicate) 検証")
    print("========================================================")
    headers = {"Origin": "http://localhost:8080"}
    r = requests.post(f"{SERVER_URL}/memory/deduplicate?session_id=test_dedup", headers=headers)
    print(f"[/memory/deduplicate] -> Status: {r.status_code}, Body: {r.text.strip()}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"

def cleanup_chats():
    chats_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend', 'data', 'chats'))
    if os.path.isdir(chats_dir):
        for f in os.listdir(chats_dir):
            if f.endswith('.json'):
                try:
                    os.remove(os.path.join(chats_dir, f))
                except Exception:
                    pass

if __name__ == '__main__':
    try:
        test_session_id_traversal()
        test_origin_null_and_csrf()
        test_tts_server_security()
        test_avatar_upload_path_removal()
        test_avatar_zip_security()
        test_silver_model_actual_load()
        test_deduplicate_api()
        print("\n🎉 全ての回帰テストが成功しました！")
    finally:
        cleanup_chats()
