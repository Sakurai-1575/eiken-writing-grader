"""構造的な保証のテスト。

レポート生成（PDF / Markdown）が API クライアントや通信ライブラリに依存していないことを
AST で検査し、「ダウンロード時に API を再呼び出しできない」ことを構造的に保証する。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "eiken_grader"

FORBIDDEN_FOR_REPORTS = (
    "requests",
    "streamlit",
    "eiken_grader.core",
    "eiken_grader.services.ocr",
    "eiken_grader.services.grading",
    "eiken_grader.ui",
)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_reports_do_not_depend_on_api_or_ui():
    files = list((SRC / "reports").glob("*.py"))
    assert files
    for f in files:
        for mod in imported_modules(f):
            assert not mod.startswith(FORBIDDEN_FOR_REPORTS), f"{f.name} imports {mod}"


def test_services_and_core_do_not_import_streamlit():
    for sub in ("services", "core", "models", "reports"):
        for f in (SRC / sub).glob("*.py"):
            assert not any(m.startswith("streamlit") for m in imported_modules(f)), f.name


def test_secrets_accessed_only_in_config():
    for f in SRC.rglob("*.py"):
        if f.name == "config.py":
            continue
        assert "st.secrets" not in f.read_text(encoding="utf-8"), f.name


def test_no_disk_writes_in_app_code():
    """答案・画像・レポートをディスクに書き出すコードが無いこと（すべてメモリ内で処理）。"""
    forbidden = ("tempfile", "write_bytes(", "write_text(", '"w"', '"wb"', "'w'", "'wb'", "os.makedirs", "mkdir(")
    for f in [*SRC.rglob("*.py"), SRC.parents[1] / "app.py"]:
        text = f.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in text, f"{f.name} contains {pattern}"
        assert "pdf.output(" not in text or "pdf.output()" in text  # 引数なし = bytes を返すだけ


def test_secrets_file_is_gitignored():
    root = Path(__file__).resolve().parents[1]
    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".streamlit/secrets.toml" in lines
