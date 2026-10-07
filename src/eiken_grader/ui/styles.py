"""画面のスタイル（iPad 縦画面 768px を基準にした教育用 Web アプリのデザイン）。

- 本文幅は最大 760px、左右 14px の余白。横スクロールを発生させない
- カード: 白背景 + 薄い枠線 + 控えめな影で統一（パネルは key 付き st.container を ``.st-key-eg-panel-*`` で装飾）
- タップしやすさ: ボタン・入力欄の高さ 48px 以上、入力欄の文字は 16px 以上（iOS Safari の自動ズーム防止）
- 英文: サンセリフ・行間 1.6 で読みやすく
- 配色は .streamlit/config.toml のライトテーマ（primary #2563eb）に合わせる
"""

from __future__ import annotations

CSS = """
<style>
:root {
    --eg-primary: #2563eb;
    --eg-primary-soft: #eff4ff;
    --eg-text: #1f2937;
    --eg-muted: #64748b;
    --eg-border: #e2e8f0;
    --eg-card: #ffffff;
    --eg-soft: #f8fafc;
    --eg-ok: #15803d;
    --eg-ok-soft: #ecfdf3;
    --eg-warn: #b45309;
    --eg-warn-soft: #fff7ed;
    --eg-bad: #dc2626;
    --eg-radius: 14px;
    --eg-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 14px rgba(15, 23, 42, 0.06);
    --eg-font-en: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial,
                  "Hiragino Sans", "Noto Sans JP", sans-serif;
}

/* ---- レイアウト ---------------------------------------------------- */
/* Streamlit の固定ヘッダー（Cloud では 60〜80px 程度）の下からコンテンツが始まるよう、
   どのバージョンのセレクタでも確実に上余白 5rem（+ ノッチ端末の safe-area）を確保する */
.block-container,
.main .block-container,
.stApp [data-testid="stMainBlockContainer"],
.stApp [data-testid="block-container"] {
    max-width: 760px;
    padding-top: calc(5rem + env(safe-area-inset-top, 0px)) !important;
    padding-bottom: 4rem;
    padding-left: 14px;
    padding-right: 14px;
}
/* 標準ヘッダーは背景を透明にし、タイトルの上に色の付いた帯が重ならないようにする */
header[data-testid="stHeader"] {
    background: transparent !important;
    box-shadow: none !important;
}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] {
    display: none;
}

/* ---- ヘッダー ------------------------------------------------------ */
.eg-header { display: flex; align-items: center; gap: 12px; margin: 0 0 14px 0; }
.eg-logo {
    width: 44px; height: 44px; flex: 0 0 auto; border-radius: 12px;
    display: flex; align-items: center; justify-content: center; font-size: 1.45rem;
    background: var(--eg-primary-soft); border: 1px solid #dbe5ff;
}
.eg-title { font-size: 1.28rem; font-weight: 800; color: var(--eg-text); line-height: 1.3; }
.eg-subtitle { font-size: 0.85rem; color: var(--eg-muted); }

/* ---- ステップインジケーター ------------------------------------------ */
.eg-stepper {
    list-style: none; display: flex; margin: 0 0 18px 0; padding: 14px 6px 12px 6px;
    background: var(--eg-card); border: 1px solid var(--eg-border); border-radius: var(--eg-radius);
    box-shadow: var(--eg-shadow);
}
.eg-stp { flex: 1 1 0; position: relative; display: flex; flex-direction: column; align-items: center; gap: 6px; }
.eg-stp + .eg-stp::before {             /* 前のステップとの連結線 */
    content: ""; position: absolute; top: 15px; right: 50%; width: calc(100% - 34px);
    margin-right: 17px; height: 3px; border-radius: 2px; background: var(--eg-border);
}
.eg-stp.done + .eg-stp::before, .eg-stp.done + .eg-stp.active::before { background: var(--eg-primary); }
.eg-stp .dot {
    position: relative; z-index: 1; width: 32px; height: 32px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 0.95rem;
    background: var(--eg-card); color: var(--eg-muted); border: 2px solid var(--eg-border);
}
.eg-stp.active .dot {
    background: var(--eg-primary); color: #fff; border-color: var(--eg-primary);
    box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.15);
}
.eg-stp.done .dot { background: var(--eg-primary-soft); color: var(--eg-primary); border-color: var(--eg-primary); }
.eg-stp .lbl { font-size: 0.8rem; color: var(--eg-muted); white-space: nowrap; }
.eg-stp.active .lbl { color: var(--eg-text); font-weight: 700; }

/* ---- パネル（カード） ------------------------------------------------ */
[class*="st-key-eg-panel-"] {
    background: var(--eg-card); border: 1px solid var(--eg-border); border-radius: var(--eg-radius);
    box-shadow: var(--eg-shadow); padding: 16px 16px 14px 16px; margin-bottom: 4px;
}
.eg-panel-title { font-weight: 800; font-size: 1.02rem; color: var(--eg-text); margin-bottom: 2px; }

/* ---- 入力欄・ボタン -------------------------------------------------- */
textarea, input, select, [data-baseweb="select"] * { font-size: 16px !important; }
.stTextArea textarea {
    font-family: var(--eg-font-en) !important; line-height: 1.6 !important;
    letter-spacing: 0.01em; padding: 12px 14px !important;
}
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    min-height: 48px; font-size: 1.02rem; font-weight: 600; border-radius: 12px;
}
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
    box-shadow: 0 2px 6px rgba(37, 99, 235, 0.25);
}
[data-testid="stFileUploaderDropzone"] { min-height: 110px; background: var(--eg-soft); }
[data-testid="stExpander"] details {
    background: var(--eg-card); border-radius: 12px; border-color: var(--eg-border);
}

/* ---- 語数バッジ ------------------------------------------------------ */
.eg-wc {
    display: inline-flex; flex-wrap: wrap; align-items: baseline; gap: 4px;
    padding: 6px 12px; border-radius: 999px; font-size: 0.95rem; margin: 2px 0 6px 0;
}
.eg-wc.ok { background: var(--eg-ok-soft); color: var(--eg-ok); }
.eg-wc.warn { background: var(--eg-warn-soft); color: var(--eg-warn); }

/* ---- 結果: 総合得点・スコアカード ------------------------------------- */
.eg-total {
    text-align: center; padding: 18px 14px; border-radius: var(--eg-radius); margin-bottom: 8px;
    background: linear-gradient(180deg, #ffffff 0%, var(--eg-primary-soft) 100%);
    border: 1px solid #dbe5ff; box-shadow: var(--eg-shadow);
}
.eg-total .num { font-size: 3rem; font-weight: 800; color: var(--eg-primary); line-height: 1.1; }
.eg-total .num small { font-size: 1.25rem; color: var(--eg-muted); font-weight: 700; }
.eg-total .sub { font-size: 0.9rem; color: var(--eg-muted); }

.eg-card {
    background: var(--eg-card); border: 1px solid var(--eg-border); border-radius: var(--eg-radius);
    box-shadow: var(--eg-shadow); padding: 12px 14px; margin-bottom: 10px; color: var(--eg-text);
}
.eg-score .head { display: flex; justify-content: space-between; align-items: baseline; font-weight: 700; }
.eg-score .score { color: var(--eg-primary); font-size: 1.35rem; font-weight: 800; }
.eg-score .score small { font-size: 0.85rem; color: var(--eg-muted); }
.eg-bar { height: 8px; border-radius: 4px; background: #e8edf5; margin: 6px 0 6px 0; overflow: hidden; }
.eg-bar > div { height: 8px; border-radius: 4px; background: var(--eg-primary); }
.eg-note { font-size: 0.88rem; color: var(--eg-muted); line-height: 1.6; }

/* ---- 結果: セクション見出し（1〜6） ------------------------------------ */
.eg-section {
    display: flex; align-items: center; gap: 10px;
    margin: 1.8rem 0 0.8rem 0; padding-bottom: 8px;
    border-bottom: 2px solid var(--eg-border);
    font-size: 1.15rem; font-weight: 800; color: var(--eg-text);
}
.eg-section .no {
    display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto;
    width: 1.75rem; height: 1.75rem; border-radius: 50%;
    background: var(--eg-primary); color: #fff; font-size: 0.95rem;
}
.eg-label { font-weight: 700; font-size: 0.95rem; color: var(--eg-text); margin: 10px 0 6px 2px; }

/* ---- 結果: 添削カード -------------------------------------------------- */
.eg-corr {
    background: var(--eg-card); border: 1px solid var(--eg-border); border-left: 5px solid var(--eg-primary);
    border-radius: 12px; box-shadow: var(--eg-shadow); padding: 10px 14px; margin: 10px 0; color: var(--eg-text);
}
.eg-corr .pair { margin: 6px 0 4px 0; font-size: 1.08rem; line-height: 1.6; font-family: var(--eg-font-en); }
.eg-corr .orig { color: var(--eg-bad); text-decoration: line-through; }
.eg-corr .arrow { margin: 0 8px; color: var(--eg-muted); }
.eg-corr .new { color: var(--eg-ok); font-weight: 700; }
.eg-corr .cat {
    font-size: 0.76rem; font-weight: 700; padding: 2px 8px; border-radius: 999px;
    background: var(--eg-primary-soft); color: var(--eg-primary);
}
.eg-corr .exp { font-size: 0.93rem; color: var(--eg-muted); line-height: 1.6; }

/* ---- テキスト表示 ------------------------------------------------------ */
.eg-mark { background: rgba(250, 204, 21, 0.4); padding: 0 3px; border-radius: 3px; }
.eg-answer { white-space: pre-wrap; line-height: 1.6; font-family: var(--eg-font-en); font-size: 1.02rem; }
.eg-card.eg-answer { background: var(--eg-soft); }
.eg-text { line-height: 1.6; }
.eg-text p { margin: 0 0 0.4rem 0; }
.eg-text ul, .eg-text ol { margin: 0.3rem 0 0.3rem 1.2rem; padding: 0; }
.eg-steps-list { padding-left: 2.2rem !important; }
.eg-steps-list li { margin: 2px 0; }

/* 長い英単語や URL でも横スクロールを発生させない */
.eg-card, .eg-corr, .eg-answer, .eg-text, .eg-total, .eg-section, .eg-wc {
    overflow-wrap: anywhere; word-break: normal; max-width: 100%; box-sizing: border-box;
}

/* 狭い画面ではステップのラベルを小さく */
@media (max-width: 420px) {
    .eg-stp .lbl { font-size: 0.68rem; }
    .eg-title { font-size: 1.1rem; }
}
</style>
"""
