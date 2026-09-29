import re

# 明示的な検索意図を示す正規表現パターン
_EXPLICIT_SEARCH_PATTERNS = [
    # 検索・ググる
    r'検索(して|する|しろ|お願い|頼む|希望|コマンド|してくれ|してみて|してほしい)?',
    r'ググ(って|る|れ|り|ろう)',
    # 調べる (「取り調べ」は除外)
    r'(?<!取り)調べ(て|る|たい|てみて|てくれ|てほしい|てよ|てごらん|てご覧)',
    # 調査
    r'調査(して|する|しろ|してみて|してほしい)',
    # 〜について (調べて/検索等)
    r'について(調べ|検索|知りたい|教えて|調査)',
    # 天気 (「いい天気」「良い天気」は除外)
    r'(?<!いい)(?<!良い)(?<!いいお)(?<!良いお)天気(予報|を教えて|はどう|教えて|について)',
    # ニュース
    r'(最新の?ニュース|ニュースを(検索|調べ|チェック))',
    # 英語: 明示的な検索指示 (search for, look up, google it)
    r'\bsearch\s+(for|about|on)\b',
    r'\blook\s+up\b',
    r'\bgoogle\s+(it|this|for)?\b',
]

_SEARCH_REGEX = re.compile('|'.join(_EXPLICIT_SEARCH_PATTERNS), re.IGNORECASE)

def should_trigger_search(text: str) -> bool:
    """
    ユーザーの入力文から明示的なWeb検索・天気意図があるかを判定する純粋関数。
    - 単なる「教えて」「いい天気」「取り調べ」等の日常会話では発火しない。
    - 「今何時」「今日は何日」等の時刻系はプロンプト注入されるため発火しない。
    - 画像添付時でも明示的な指示（「検索して」「調べて」等）が必要。
    """
    if not text or not isinstance(text, str):
        return False
    return bool(_SEARCH_REGEX.search(text))
