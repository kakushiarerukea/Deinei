import re

# Windows予約デバイス名 (大文字小文字を問わない)
# CON, PRN, AUX, NUL, COM1-COM9, LPT1-LPT9
WINDOWS_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
}

def is_safe_session_id(session_id: str) -> bool:
    """
    セッションIDの厳格な検証関数。
    - 文字列型、1〜64文字
    - 半角英数字、ハイフン(-)、アンダースコア(_)のみ許容
    - 末尾のドット・空白、特殊文字を拒絶
    - Windows予約デバイス名 (CON, PRN, AUX, NUL, COM1〜9, LPT1〜9) を大文字小文字・拡張子問わず拒絶
    """
    if not session_id or not isinstance(session_id, str):
        return False

    # 前後の空白・末尾ドットを拒否
    if session_id != session_id.strip() or session_id.endswith('.'):
        return False

    # 1〜64文字の半角英数字、ハイフン、アンダースコアのみ
    if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', session_id):
        return False

    # Windows予約名チェック (全体およびドット前のベース名)
    clean_upper = session_id.upper()
    stem_upper = clean_upper.split('.')[0]
    if clean_upper in WINDOWS_RESERVED_NAMES or stem_upper in WINDOWS_RESERVED_NAMES:
        return False

    return True
