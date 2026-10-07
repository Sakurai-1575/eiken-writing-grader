# ✍️ 英検ライティング 採点アシスタント

手書きの英検ライティング答案を iPad で撮影 → AI が文字起こし → 確認・修正 → 英検の4観点（内容・構成・語彙・文法）で採点・添削し、A4・2ページの PDF / Markdown レポートを出力する学習支援 Web アプリです。

- 対象: 英検 **準2級**（Eメール / 意見論述）・**2級**（要約 / 意見論述）・**準1級**（要約 / 意見論述）
- 費用: **0 円**（Google AI Studio 無料枠 + Streamlit Community Cloud）
- データ保持: **なし**（DB 不使用・ファイル保存なし。セッション終了で消去）

> 詳細な設計は [PLAN.md](PLAN.md) を参照してください。

---

## 1. ローカルで起動する（Windows / PowerShell）

```powershell
cd C:\desktop\英検ライティング採点ツール
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

`.streamlit/secrets.toml` を作成します（`secrets.toml.example` をコピー）。

```toml
GEMINI_API_KEY = "Google AI Studio で発行した API キー"
# GEMINI_MODEL = "gemini-3.5-flash-lite"   # 任意: モデル名の上書き
# APP_PASSCODE = "任意のパスコード"     # 任意: 設定するとパスコード画面を表示
```

```powershell
pytest -q                  # 単体テスト（実 API は呼びません）
streamlit run app.py       # http://localhost:8501
```

- 社内プロキシ等の環境変数が設定されていても、アプリはプロキシを経由せず直接 Google API に接続します。
- iPad 実機で試す場合: `streamlit run app.py --server.address 0.0.0.0` とし、同じ Wi-Fi の iPad から `http://<PCのIPアドレス>:8501` を開きます（Windows ファイアウォールの許可が必要）。
  ブラウザのカメラ機能は HTTPS が必要なため、ローカルでは「写真をアップロード（写真を撮る）」をお使いください。

## 2. Streamlit Community Cloud に公開する

1. GitHub にリポジトリを作成して push します。
   **push 前に** `git ls-files | findstr secrets` で `secrets.toml` が含まれていないことを確認してください（`.gitignore` で除外済み）。
2. https://share.streamlit.io で **New app** → リポジトリ・ブランチ・`app.py` を指定。
3. **Advanced settings** で Python 3.12 を選択し、**Secrets** に上記の TOML を貼り付けます。
4. 無料枠を第三者に使われないよう、`APP_PASSCODE` を設定するか、アプリを private（招待者のみ）にすることを推奨します。

## 3. 設定の変更

| ファイル | 内容 |
|---|---|
| `config/settings.toml` | モデル名（既定 `gemini-3.5-flash-lite`）とフォールバック先（`gemini-flash-latest`）、開発モード（`[debug]`）、タイムアウト、再試行回数、レート制限（RPM/RPD・クールダウン）、画像サイズ |
| `config/rubrics.toml` | 級・問題形式・語数目安・採点観点。**級の追加はこのファイルへの追記のみで可能**（3級・1級の雛形あり） |
| `.streamlit/config.toml` | アップロード上限、テーマ、利用統計の無効化 |

モデル名の優先順位: Secrets の `GEMINI_MODEL` ＞ `settings.toml` ＞ 既定値。

## 4. テスト

```powershell
pytest -q                                    # 全テスト（API はモック）
pytest --cov=eiken_grader --cov-report=term  # カバレッジ
pytest -m live                               # 実 API 疎通確認（API を 1 回消費）
ruff check .                                 # 静的解析
```

## 5. 注意事項

- 答案用紙に**氏名・学校名を書かない／写さない**でください。
- Google AI Studio の無料枠では、送信内容が Google のサービス改善に利用される場合があります。
- AI による参考採点であり、実際の英検の得点を保証するものではありません。
- PDF 用フォント: Noto Sans JP（SIL Open Font License 1.1、`assets/fonts/OFL.txt`）。
