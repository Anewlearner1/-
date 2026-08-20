"""
載入專案根目錄的 .env。

必須在其他專案模組之前匯入 —— discussion.py 與 notifier.py 在模組層級就會讀取
環境變數，太晚載入 .env 會讀不到。沒安裝 python-dotenv 時靜默略過，
仍可用一般環境變數（export / setx）的方式提供設定。
"""
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover — 未安裝 python-dotenv 時退回系統環境變數
    pass
