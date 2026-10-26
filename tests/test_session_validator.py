import os, sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend', 'data')))
from session_validator import is_safe_session_id

def test_valid_ids():
    assert is_safe_session_id("default_session") is True
    assert is_safe_session_id("session-123") is True
    assert is_safe_session_id("abc_DEF-456") is True
    assert is_safe_session_id("a") is True
    assert is_safe_session_id("a" * 64) is True

def test_invalid_lengths_and_types():
    assert is_safe_session_id("") is False
    assert is_safe_session_id(None) is False
    assert is_safe_session_id(123) is False
    assert is_safe_session_id("a" * 65) is False

def test_path_traversal_and_symbols():
    assert is_safe_session_id("../../evil") is False
    assert is_safe_session_id("..\\..\\evil") is False
    assert is_safe_session_id("evil/test") is False
    assert is_safe_session_id("evil\\test") is False
    assert is_safe_session_id("session;drop") is False
    assert is_safe_session_id("session name") is False
    assert is_safe_session_id("test*") is False
    assert is_safe_session_id("abc$def") is False

def test_windows_reserved_names():
    # 大文字小文字
    for name in ["CON", "con", "Con", "PRN", "prn", "AUX", "aux", "NUL", "nul", "NuL"]:
        assert is_safe_session_id(name) is False, f"Failed for {name}"
    for i in range(1, 10):
        assert is_safe_session_id(f"COM{i}") is False
        assert is_safe_session_id(f"com{i}") is False
        assert is_safe_session_id(f"LPT{i}") is False
        assert is_safe_session_id(f"lpt{i}") is False

def test_windows_reserved_with_extensions_and_dots():
    assert is_safe_session_id("NUL.json") is False
    assert is_safe_session_id("con.txt") is False
    assert is_safe_session_id("nul.") is False
    assert is_safe_session_id(" session ") is False

if __name__ == '__main__':
    test_valid_ids()
    test_invalid_lengths_and_types()
    test_path_traversal_and_symbols()
    test_windows_reserved_names()
    test_windows_reserved_with_extensions_and_dots()
    print("✅ All session_validator tests passed successfully!")
