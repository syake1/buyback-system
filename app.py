"""
app.py  ── Streamlit フロントエンド
自社株買いスクリーナーをブラウザで操作できるWebアプリ。

【起動方法（ローカル）】
    streamlit run app.py

【Streamlit Cloud】
    GitHubにpushするだけで自動デプロイされる。
"""

import streamlit as st
import pandas as pd
from datetime import datetime
from pathlib import Path

# ページ設定（必ず最初に呼ぶ）
st.set_page_config(
    page_title="自社株買いスクリーナー",
    page_icon="📈",
    layout="wide",
)

# ──────────────────────────────────────────────
# スタイル
# ──────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #0e1117; }
    .signal-card {
        background: linear-gradient(135deg, #1a1f2e, #1e2a3a);
        border: 1px solid #2a3f55;
        border-left: 4px solid #00d4aa;
        border-radius: 8px;
        padding: 16px 20px;
        margin-bottom: 12px;
    }
    .no-signal-card {
        background: #1a1f2e;
        border: 1px solid #2a3f55;
        border-left: 4px solid #555;
        border-radius: 8px;
        padding: 16px 20px;
        margin-bottom: 12px;
    }
    .score-badge {
        background: #00d4aa;
        color: #000;
        font-weight: bold;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.85em;
    }
    .tag {
        background: #1e3a5f;
        color: #7ec8e3;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.78em;
        margin-right: 4px;
    }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────
# ヘッダー
# ──────────────────────────────────────────────
st.title("📈 自社株買いスクリーナー")
st.caption("TDnet適時開示から自社株買い銘柄を検知し、RSI底打ちシグナルを判定します")

st.divider()

# ──────────────────────────────────────────────
# サイドバー：設定パネル
# ──────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 設定")

    mode = st.radio(
        "検索モード",
        ["本日の開示のみ", "過去履歴を遡る"],
        help="平日15:30以降に「本日の開示のみ」を使うと最新の自社株買いを検知できます"
    )

    if mode == "過去履歴を遡る":
        days = st.slider("遡る営業日数", min_value=5, max_value=60, value=30, step=5)
    else:
        days = 1

    st.divider()
    st.subheader("スクリーニング条件")

    market_cap_threshold = st.number_input(
        "時価総額下限（億円）",
        min_value=100,
        max_value=10000,
        value=1000,
        step=100,
    )

    rsi_score_threshold = st.slider(
        "RSIスコア閾値（/6点）",
        min_value=1,
        max_value=6,
        value=3,
        help="この点数以上の銘柄をシグナルとして表示します"
    )

    run_rsi = st.checkbox("RSI分析も実行する", value=True)

    st.divider()
    run_button = st.button("🔍 スクリーニング実行", type="primary", use_container_width=True)

# ──────────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────────
if run_button:

    # インポート（実行時のみ行う）
    try:
        from buyback_screener import BuybackScreener, MarketCapFetcher
        from tdnet_history import HistoryScreener
        from rsi_analyzer import RSIAnalyzer, AutoFetcher, RSIConfig
    except ImportError as e:
        st.error(f"モジュールの読み込みに失敗しました: {e}")
        st.stop()

    # ── フェーズ1: スクリーニング ──
    with st.spinner("TDnetから開示情報を取得中..."):
        try:
            if mode == "本日の開示のみ":
                screener = BuybackScreener()
                stocks = screener.run()
            else:
                screener = HistoryScreener(days=days)
                stocks = screener.run()
        except Exception as e:
            st.error(f"スクリーニング中にエラーが発生しました: {e}")
            st.stop()

    if not stocks:
        st.warning("スクリーニングを通過した銘柄はありませんでした。")
        if mode == "本日の開示のみ":
            st.info("💡 今日が休場日または開示がない場合は「過去履歴を遡る」モードをお試しください。")
        st.stop()

    st.success(f"✅ {len(stocks)}銘柄がフェーズ1（時価総額・市場外買付）を通過しました")

    # ── フェーズ2: RSI分析 ──
    if run_rsi:
        with st.spinner(f"{len(stocks)}銘柄のRSIを分析中...（1銘柄あたり約2秒）"):
            try:
                config   = RSIConfig(score_threshold=rsi_score_threshold)
                analyzer = RSIAnalyzer(fetcher=AutoFetcher(), config=config)
                results  = analyzer.analyze_batch(
                    [{"code": s.code, "name": s.name} for s in stocks]
                )
            except Exception as e:
                st.error(f"RSI分析中にエラーが発生しました: {e}")
                st.stop()

        # ── 結果表示 ──
        signal_stocks   = [r for r in results if r.is_signal]
        no_signal_stocks = [r for r in results if not r.is_signal]

        # サマリー指標
        col1, col2, col3 = st.columns(3)
        col1.metric("スクリーニング通過", f"{len(stocks)}銘柄")
        col2.metric("RSIシグナル発生", f"{len(signal_stocks)}銘柄")
        col3.metric("実行日時", datetime.now().strftime("%m/%d %H:%M"))

        st.divider()

        # シグナルあり
        if signal_stocks:
            st.subheader("🟢 RSIシグナル発生銘柄")
            for r in signal_stocks:
                tags_html = "".join(
                    f'<span class="tag">{s}</span>' for s in r.signals_hit
                )
                st.markdown(f"""
                <div class="signal-card">
                    <b>[{r.code}] {r.name}</b>
                    &nbsp;&nbsp;<span class="score-badge">{r.score}点</span><br>
                    <small>RSI: {r.rsi_current} &nbsp;|&nbsp; 株価: {r.price_current}円</small><br>
                    <div style="margin-top:6px">{tags_html}</div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("現時点でRSIシグナルが発生している銘柄はありません。")

        # シグナルなし（折りたたみ）
        if no_signal_stocks:
            with st.expander(f"シグナルなし銘柄（{len(no_signal_stocks)}件）"):
                for r in no_signal_stocks:
                    st.markdown(f"""
                    <div class="no-signal-card">
                        <b>[{r.code}] {r.name}</b>
                        &nbsp;&nbsp;<small>RSI: {r.rsi_current} | スコア: {r.score}点</small>
                    </div>
                    """, unsafe_allow_html=True)

        # CSV ダウンロード
        st.divider()
        df = pd.DataFrame([{
            "銘柄コード": r.code,
            "銘柄名":    r.name,
            "RSI":       r.rsi_current,
            "株価":      r.price_current,
            "スコア":    r.score,
            "シグナル":  r.is_signal,
            "発火シグナル": ", ".join(r.signals_hit),
        } for r in results])

        st.download_button(
            label="📥 結果をCSVでダウンロード",
            data=df.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"buyback_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )

    else:
        # RSI分析なし：フェーズ1結果のみ表示
        st.subheader("スクリーニング通過銘柄")
        df = pd.DataFrame([{
            "銘柄コード":   s.code,
            "銘柄名":      s.name,
            "時価総額(億円)": round(s.market_cap / 1e8, 1),
            "発表日":      s.disclosed_at,
        } for s in stocks])
        st.dataframe(df, use_container_width=True)

else:
    # 初期画面
    st.markdown("""
    ### 使い方
    1. 左のサイドバーで**検索モード**と**条件**を設定
    2. **「スクリーニング実行」**ボタンを押す
    3. 結果を確認してCSVでダウンロード

    ### 検索モードの違い
    | モード | 用途 |
    |--------|------|
    | 本日の開示のみ | 平日15:30以降に実行。当日の自社株買い開示を検知 |
    | 過去履歴を遡る | 現在進行中の自社株買いをまとめて確認したいとき |

    ### RSIシグナルの見方
    | スコア | 意味 |
    |--------|------|
    | 5〜6点 | 強いシグナル。複数の底打ち条件が重なっている |
    | 3〜4点 | 標準シグナル。エントリー候補として検討 |
    | 1〜2点 | 弱いシグナル。様子見を推奨 |
    """)
