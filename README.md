# 自社株買いスクリーナー フェーズ1 - 実行ガイド

## セットアップ

```bash
pip install -r requirements.txt
```

## 実行方法

```bash
python buyback_screener.py
```

結果はコンソールに表示され、同時に `buyback_candidates_YYYYMMDD_HHMMSS.csv` として保存されます。

---

## アーキテクチャ概要

```
BuybackScreener（統合・拡張の起点）
├── TDnetFetcher       # TDnetから開示情報を取得・PDFダウンロード
├── PDFAnalyzer        # PDFから市場外買付キーワードを検出
└── MarketCapFetcher   # yfinanceで時価総額を取得・閾値判定
```

フェーズ2以降は `BuybackScreener` に `TechnicalAnalyzer` や `OrderManager` を追加注入する設計です。

---

## 重要な注意事項

### TDnetへのアクセス制限について
- TDnetは商用利用・大量アクセスを **禁止** しています
- `TDNET_SLEEP_SEC = 1.0` で各リクエスト間に必ずインターバルを設けてください
- 本番運用では **1日1回（例: 15:30以降）** のバッチ実行を推奨します
- HTMLの構造が変更された場合は `TDnetFetcher.fetch_disclosures()` の CSS セレクタを修正してください

> **代替案**: TDnetはRSSフィードも提供しています。大量スクレイピングを避けたい場合は  
> `https://www.release.tdnet.info/inbs/I_rssFeed.html` のRSSを利用する方法も検討してください。

### yfinanceの日本株コードについて
- yfinanceでは日本株に `.T` サフィックスが必要です（例: `7203` → `7203.T`）
- `marketCap` は **USD建て** で返ってくることが多いため、コード内でUSD/JPY換算を行っています
- yfinanceは非公式ライブラリのため、Yahoo Finance側の変更で動作が不安定になることがあります
- **本番環境では J-Quants API（金融庁）や Bloomberg API の利用を強く推奨します**

### PDFの文字コードについて
- 一部の古いPDFはフォント埋め込みの関係でテキスト抽出ができない場合があります
- その場合は `pdfplumber` が失敗し `PyPDF2` にフォールバックします
- それでも抽出できない場合は OCR（`pytesseract`）の導入を検討してください

---

## フェーズ2以降の拡張イメージ

```python
# フェーズ2: テクニカル分析の追加
class TechnicalAnalyzer:
    def analyze_daily(self, code: str) -> dict:
        """日足RSI・窓埋め状態を分析"""
        ...
    def analyze_5min(self, code: str) -> dict:
        """5分足RSIの底打ち判定"""
        ...

# フェーズ3: 自動発注
class OrderManager:
    def submit_order(self, stock: ScreenedStock, price: float) -> None:
        """証券会社API経由で発注"""
        ...

# 統合
screener = BuybackScreener()
screener.technical_analyzer = TechnicalAnalyzer()
screener.order_manager = OrderManager(api_key="YOUR_KEY")
results = screener.run()
```
