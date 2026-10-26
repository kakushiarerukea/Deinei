import os, sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend', 'data')))
from search_trigger import should_trigger_search

def test_should_trigger():
    # 明示的な検索意図（発火すべき例）
    positive_cases = [
        "東京の天気を教えて",
        "最新のニュースを検索して",
        "Pythonについて調べて",
        "〜について調べて",
        "この本について調べて",
        "ググって",
        "search for X",
        "look up X",
        "東京の天気予報",
        "明日の天気はどう？",
        "流行りの映画を教えて",
        "おすすめの観光地を教えて",
    ]
    for text in positive_cases:
        assert should_trigger_search(text) is True, f"Failed positive trigger for: '{text}'"

def test_should_not_trigger():
    # 日常会話・曖昧表現（発火してはならない例）
    negative_cases = [
        "教えて",
        "それ教えてよ",
        "これ教えて",
        "あれ教えて",
        "いい天気だね",
        "良い天気ですね",
        "取り調べみたい",
        "tell me more",
        "latest",
        "今何時",
        "今日は何日",
        "今日は何曜日",
        "こんにちは",
        "ありがとう",
        "誰？",
        "何これ",
        "この本おもしろいね",
    ]
    for text in negative_cases:
        assert should_trigger_search(text) is False, f"Failed negative trigger for: '{text}'"

if __name__ == '__main__':
    test_should_trigger()
    test_should_not_trigger()
    print("✅ All search trigger tests passed successfully!")
