"""iPad 縦画面（768px）向けの CSS。

- 本文幅を 740px 程度に制限し、左右に 14px の余白
- ボタン・入力欄の高さ 48px 以上（タップしやすさ）
- 入力欄の文字は 16px 以上（iOS Safari の自動ズーム防止）
- サイドバーは使わない
- 色は半透明のアクセントのみ使い、ライト / ダーク両テーマで読めるようにする
"""

from __future__ import annotations

CSS = """
<style>
.block-container {
    max-width: 760px;
    padding-top: 2.2rem;
    padding-bottom: 4rem;
    padding-left: 14px;
    padding-right: 14px;
}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] {
    display: none;
}
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    min-height: 48px;
    font-size: 1.02rem;
    border-radius: 10px;
}
textarea, input, select, [data-baseweb="select"] * {
    font-size: 16px !important;
}
.stTextArea textarea {
    line-height: 1.65;
}
[data-testid="stFileUploaderDropzone"] {
    min-height: 120px;
}

/* ステップ表示 */
.eg-steps { display: flex; gap: 6px; margin: 0 0 1rem 0; }
.eg-step {
    flex: 1; text-align: center; padding: 8px 2px; border-radius: 8px; font-size: 0.85rem;
    border: 1px solid rgba(128,128,128,0.35); opacity: 0.6;
}
.eg-step.active { border-color: #2563eb; background: rgba(37,99,235,0.12); opacity: 1; font-weight: 700; }
.eg-step.done { opacity: 0.9; }

/* スコアカード */
.eg-total {
    text-align: center; padding: 14px; border-radius: 12px; margin-bottom: 12px;
    background: rgba(37,99,235,0.10); border: 1px solid rgba(37,99,235,0.35);
}
.eg-total .num { font-size: 2.6rem; font-weight: 800; color: #2563eb; line-height: 1.1; }
.eg-total .sub { font-size: 0.9rem; opacity: 0.8; }
.eg-card {
    border: 1px solid rgba(128,128,128,0.35); border-radius: 12px; padding: 10px 12px; margin-bottom: 10px;
}
.eg-card .head { display: flex; justify-content: space-between; font-weight: 700; }
.eg-card .score { color: #2563eb; }
.eg-bar { height: 8px; border-radius: 4px; background: rgba(128,128,128,0.25); margin: 6px 0 2px 0; }
.eg-bar > div { height: 8px; border-radius: 4px; background: #2563eb; }

/* 結果画面のセクション見出し（1〜6） */
.eg-section {
    display: flex; align-items: center; gap: 10px;
    margin: 1.6rem 0 0.7rem 0; padding-bottom: 6px;
    border-bottom: 2px solid rgba(128,128,128,0.35);
    font-size: 1.15rem; font-weight: 800;
}
.eg-section .no {
    display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto;
    width: 1.7rem; height: 1.7rem; border-radius: 50%;
    background: #2563eb; color: #fff; font-size: 0.95rem;
}

/* 添削カード */
.eg-corr {
    border: 1px solid rgba(128,128,128,0.35); border-left: 5px solid rgba(37,99,235,0.7);
    border-radius: 10px; padding: 8px 12px; margin: 10px 0;
}
.eg-corr .pair { margin: 4px 0 2px 0; font-size: 1.05rem; line-height: 1.6; }
.eg-corr .orig { color: #dc2626; text-decoration: line-through; }
.eg-corr .arrow { margin: 0 8px; opacity: 0.7; }
.eg-corr .new { color: #16a34a; font-weight: 700; }
.eg-corr .cat { font-size: 0.78rem; padding: 1px 6px; border-radius: 6px; background: rgba(128,128,128,0.18); }
.eg-corr .exp { font-size: 0.95rem; opacity: 0.9; margin-top: 2px; }
.eg-mark { background: rgba(250, 204, 21, 0.35); padding: 0 3px; border-radius: 3px; }
.eg-answer { white-space: pre-wrap; line-height: 1.7; }
.eg-text { line-height: 1.7; }
.eg-text ul, .eg-text ol { margin: 0.3rem 0 0.3rem 1.2rem; padding: 0; }
.eg-note { font-size: 0.85rem; opacity: 0.75; }

/* 長い英単語や URL でも横スクロールを発生させない */
.eg-card, .eg-corr, .eg-answer, .eg-text, .eg-total, .eg-section {
    overflow-wrap: anywhere; word-break: normal; max-width: 100%; box-sizing: border-box;
}
</style>
"""
