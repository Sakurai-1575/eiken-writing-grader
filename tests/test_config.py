from __future__ import annotations

import pytest

from eiken_grader.config import DEFAULT_MODEL, get_api_key, load_rubrics, load_settings
from eiken_grader.errors import ApiKeyMissingError, ConfigError


def test_default_model():
    assert DEFAULT_MODEL == "gemini-flash-latest"
    assert load_settings().gemini.model == "gemini-flash-latest"


def test_model_from_settings_file(tmp_path):
    p = tmp_path / "settings.toml"
    p.write_text('[gemini]\nmodel = "gemini-other-model"\n', encoding="utf-8")
    assert load_settings(p).gemini.model == "gemini-other-model"


def test_secret_model_overrides_settings_file(tmp_path):
    p = tmp_path / "settings.toml"
    p.write_text('[gemini]\nmodel = "from-file"\n', encoding="utf-8")
    assert load_settings(p, secrets={"GEMINI_MODEL": " from-secret "}).gemini.model == "from-secret"
    assert load_settings(p, secrets={"GEMINI_MODEL": "  "}).gemini.model == "from-file"


def test_missing_sections_use_defaults(tmp_path):
    p = tmp_path / "settings.toml"
    p.write_text("", encoding="utf-8")
    s = load_settings(p)
    assert s.gemini.model == DEFAULT_MODEL
    assert s.rate_limit.rpm >= 1


@pytest.mark.parametrize(
    "content",
    [
        "[rate_limit]\nrpm = 0\n",
        '[gemini]\napi_base = "http://insecure.example.com"\n',
        '[gemini]\nschema_mode = "xml"\n',
        "[image]\njpeg_quality = 5\n",
    ],
)
def test_invalid_settings_raise_config_error(tmp_path, content):
    p = tmp_path / "settings.toml"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(p)


def test_broken_toml_raises_config_error(tmp_path):
    p = tmp_path / "settings.toml"
    p.write_text("[gemini\nmodel=", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(p)


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "nope.toml")


def test_api_key():
    assert get_api_key({"GEMINI_API_KEY": " abc "}) == "abc"
    for secrets in ({}, {"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "   "}, {"GEMINI_API_KEY": 123}):
        with pytest.raises(ApiKeyMissingError):
            get_api_key(secrets)


# --- rubrics ---------------------------------------------------------------
def test_initial_grades_are_pre2_g2_pre1(rubrics):
    assert [g.label for g in rubrics.enabled_grades()] == ["準2級", "2級", "準1級"]


@pytest.mark.parametrize(
    ("grade_id", "task_id", "word_range"),
    [
        ("pre2", "email", (40, 50)),
        ("pre2", "opinion", (50, 60)),
        ("g2", "summary", (45, 55)),
        ("g2", "opinion", (80, 100)),
        ("pre1", "summary", (60, 70)),
        ("pre1", "opinion", (120, 150)),
    ],
)
def test_tasks_have_four_criteria(rubrics, grade_id, task_id, word_range):
    _, task = rubrics.get_task(grade_id, task_id)
    assert task.criterion_names == ["内容", "構成", "語彙", "文法"]
    assert task.word_range == word_range
    assert task.max_total == 16


def test_unknown_task_raises(rubrics):
    with pytest.raises(ConfigError):
        rubrics.get_task("g2", "nope")


def test_new_grade_can_be_added_by_config_only(tmp_path):
    p = tmp_path / "rubrics.toml"
    p.write_text(
        """
[score_levels]
"1" = "x"
[grades.g3]
label = "3級"
order = 10
[grades.g3.tasks.email]
label = "Eメール"
word_range = [15, 25]
max_per_criterion = 4
[grades.g3.tasks.email.criteria]
"内容" = "a"
"語彙" = "b"
"文法" = "c"
[grades.g1]
label = "1級"
enabled = false
order = 60
""",
        encoding="utf-8",
    )
    r = load_rubrics(p)
    assert [g.id for g in r.enabled_grades()] == ["g3"]
    _, task = r.get_task("g3", "email")
    assert task.max_total == 12


def test_invalid_word_range_rejected(tmp_path):
    p = tmp_path / "rubrics.toml"
    p.write_text(
        '[grades.x]\nlabel="x"\n[grades.x.tasks.t]\nlabel="t"\nword_range=[50, 10]\n'
        '[grades.x.tasks.t.criteria]\n"内容"="a"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_rubrics(p)


def test_no_enabled_grades_rejected(tmp_path):
    p = tmp_path / "rubrics.toml"
    p.write_text('[grades.x]\nlabel="x"\nenabled=false\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_rubrics(p)
