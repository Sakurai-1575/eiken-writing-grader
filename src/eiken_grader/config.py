"""設定（config/settings.toml）とルーブリック（config/rubrics.toml）の読み込み。

- 設定値は Pydantic で検証し、不正値は ConfigError として起動時に検出する。
- Secrets へのアクセスは ``read_secrets`` / ``get_api_key`` に集約する。
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from eiken_grader.errors import ApiKeyMissingError, ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
SETTINGS_PATH = CONFIG_DIR / "settings.toml"
RUBRICS_PATH = CONFIG_DIR / "rubrics.toml"
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"

DEFAULT_MODEL = "gemini-3.5-flash-lite"


# ---------------------------------------------------------------------------
# settings.toml
# ---------------------------------------------------------------------------
class GeminiSettings(BaseModel):
    model: str = DEFAULT_MODEL
    # 主モデルが混雑（503 等）・枠超過（429）・タイムアウトで失敗したときに順に試すモデル
    fallback_models: list[str] = []
    api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    schema_mode: Literal["json_schema", "openapi"] = "json_schema"
    timeout_sec: float = Field(default=90, gt=0, le=600)
    temperature_ocr: float = Field(default=0.0, ge=0, le=2)
    temperature_grading: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=16384, ge=256)
    max_retries: int = Field(default=2, ge=0, le=5)
    backoff_base_sec: float = Field(default=4.0, ge=0)
    backoff_max_sec: float = Field(default=30.0, ge=0)

    @field_validator("model")
    @classmethod
    def _model_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("model must not be empty")
        return v

    @field_validator("fallback_models")
    @classmethod
    def _strip_fallbacks(cls, v: list[str]) -> list[str]:
        return [m.strip() for m in v if m.strip()]

    @field_validator("api_base")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("api_base must start with https://")
        return v.rstrip("/")

    @property
    def model_chain(self) -> list[str]:
        """試行するモデルの順序（主モデル → フォールバック。重複は除く）。"""
        return list(dict.fromkeys([self.model, *self.fallback_models]))


class RateLimitSettings(BaseModel):
    rpm: int = Field(default=8, ge=1)
    rpd: int = Field(default=200, ge=1)
    cooldown_sec: float = Field(default=0.5, ge=0)


class ImageSettings(BaseModel):
    max_upload_mb: int = Field(default=10, ge=1, le=200)
    max_images: int = Field(default=2, ge=1, le=5)
    max_long_edge_px: int = Field(default=2000, ge=512, le=4096)
    jpeg_quality: int = Field(default=85, ge=50, le=95)


class GradingSettings(BaseModel):
    max_corrections: int = Field(default=10, ge=0, le=20)


class DebugSettings(BaseModel):
    # "auto": ローカル実行時のみ ON（Streamlit Community Cloud 上では OFF） / "on" / "off"
    mode: Literal["auto", "on", "off"] = "auto"


class AppSettings(BaseModel):
    gemini: GeminiSettings = GeminiSettings()
    rate_limit: RateLimitSettings = RateLimitSettings()
    image: ImageSettings = ImageSettings()
    grading: GradingSettings = GradingSettings()
    debug: DebugSettings = DebugSettings()


# Streamlit Community Cloud ではリポジトリが /mount/src/<repo> に配置される
CLOUD_MOUNT_PREFIX = "/mount/src"


def is_debug_mode(settings: AppSettings, secrets: Mapping[str, Any] | None = None, cwd: str | None = None) -> bool:
    """開発モード（詳細エラーを端末ログ・画面に表示）かどうか。

    優先順位: secrets["DEBUG"]（true/false） > settings.toml の debug.mode > auto 判定
    """
    override = (secrets or {}).get("DEBUG")
    if isinstance(override, bool):
        return override
    if settings.debug.mode != "auto":
        return settings.debug.mode == "on"
    here = (cwd if cwd is not None else str(Path.cwd())).replace("\\", "/")
    return not here.startswith(CLOUD_MOUNT_PREFIX)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"設定ファイルが見つかりません: {path.name}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"設定ファイルの書式が正しくありません: {path.name}（{e}）") from e


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0]
    loc = ".".join(str(p) for p in err["loc"])
    return f"{loc}: {err['msg']}"


def load_settings(path: Path = SETTINGS_PATH, secrets: Mapping[str, Any] | None = None) -> AppSettings:
    """settings.toml を読み込む。

    モデル名の解決順: secrets["GEMINI_MODEL"] > settings.toml の gemini.model > DEFAULT_MODEL
    """
    data = _read_toml(path)
    model_override = (secrets or {}).get("GEMINI_MODEL")
    if isinstance(model_override, str) and model_override.strip():
        data.setdefault("gemini", {})["model"] = model_override.strip()
    try:
        return AppSettings.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"設定値が不正です（{path.name}）: {_first_error(e)}") from e


# ---------------------------------------------------------------------------
# rubrics.toml
# ---------------------------------------------------------------------------
class TaskRubric(BaseModel):
    id: str
    label: str
    word_range: tuple[int, int]
    max_per_criterion: int = Field(default=4, ge=1, le=10)
    question_hint: str = ""
    task_instruction: str = ""
    criteria: dict[str, str]

    @field_validator("criteria")
    @classmethod
    def _criteria_not_empty(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("criteria must not be empty")
        return v

    @model_validator(mode="after")
    def _valid_range(self) -> TaskRubric:
        lo, hi = self.word_range
        if not 0 < lo <= hi:
            raise ValueError("word_range must satisfy 0 < min <= max")
        return self

    @property
    def criterion_names(self) -> list[str]:
        return list(self.criteria)

    @property
    def max_total(self) -> int:
        return self.max_per_criterion * len(self.criteria)


class GradeRubric(BaseModel):
    id: str
    label: str
    enabled: bool = True
    order: int = 0
    cefr: str = ""
    tasks: dict[str, TaskRubric] = {}


class Rubrics(BaseModel):
    score_levels: dict[str, str]
    grades: dict[str, GradeRubric]

    def enabled_grades(self) -> list[GradeRubric]:
        """UI に表示する級（enabled かつ問題形式が 1 つ以上）を order 順で返す。"""
        return sorted((g for g in self.grades.values() if g.enabled and g.tasks), key=lambda g: g.order)

    def get_task(self, grade_id: str, task_id: str) -> tuple[GradeRubric, TaskRubric]:
        try:
            grade = self.grades[grade_id]
            return grade, grade.tasks[task_id]
        except KeyError as e:
            raise ConfigError(f"ルーブリックに存在しない級・形式です: {grade_id}/{task_id}") from e


def load_rubrics(path: Path = RUBRICS_PATH) -> Rubrics:
    data = _read_toml(path)
    try:
        grades: dict[str, GradeRubric] = {}
        for gid, g in (data.get("grades") or {}).items():
            tasks = {
                tid: TaskRubric.model_validate({"id": tid, **t}) for tid, t in (g.get("tasks") or {}).items()
            }
            grades[gid] = GradeRubric.model_validate({**g, "id": gid, "tasks": tasks})
        rubrics = Rubrics(score_levels=data.get("score_levels") or {}, grades=grades)
    except ValidationError as e:
        raise ConfigError(f"ルーブリックの設定が不正です（{path.name}）: {_first_error(e)}") from e
    if not rubrics.enabled_grades():
        raise ConfigError("有効な級が 1 つもありません（rubrics.toml の enabled を確認してください）。")
    return rubrics


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
def read_secrets() -> dict[str, Any]:
    """st.secrets を dict として返す。secrets.toml が無い環境では空 dict。"""
    try:
        import streamlit as st

        return {k: st.secrets[k] for k in st.secrets}
    except Exception:  # secrets.toml 不在時は StreamlitSecretNotFoundError 等
        return {}


def get_api_key(secrets: Mapping[str, Any]) -> str:
    """Secrets の GEMINI_API_KEY を返す。未設定なら ApiKeyMissingError。"""
    key = secrets.get("GEMINI_API_KEY")
    if not isinstance(key, str) or not key.strip():
        raise ApiKeyMissingError()
    return key.strip()
