"""Tests for the pretty-printer in view.py."""

from __future__ import annotations

from view import pretty_mxcfg_view, render_scalar


def test_render_scalar_booleans():
    assert render_scalar(True) == "✅ включено"
    assert render_scalar(False) == "❌ выключено"


def test_render_scalar_none():
    assert render_scalar(None) == "null"


def test_render_scalar_escapes_html_in_strings():
    assert render_scalar('a<b>"c"&d') == '"a&lt;b&gt;"c"&amp;d"'


def test_render_scalar_numbers():
    assert render_scalar(42) == "42"
    assert render_scalar(3.14) == "3.14"


def test_pretty_mxcfg_view_includes_known_fields():
    parsed = {
        "author": "alice",
        "description": "demo",
        "encrypted": False,
        "AfterDur": 1000,
        "scriptMode": "auto",
        "data": {"ZaderPC": 100, "Auto": True},
        "steps": [{"d": 50, "dp": 10}],
        "onStop": "stop",
    }

    rendered = pretty_mxcfg_view(parsed)

    assert "👤 Автор" in rendered
    assert '"alice"' in rendered
    assert "⏱ После таймера" in rendered
    assert "1000 мс" in rendered
    assert "🌐 Имитация сети" in rendered
    assert "Задержка пакетов клиента" in rendered
    assert "📋 Шаги" in rendered
    assert "Шаг 1" in rendered
    assert "🛑 При остановке" in rendered


def test_pretty_mxcfg_view_renders_unknown_fields():
    parsed = {"author": "a", "customField": "value"}
    rendered = pretty_mxcfg_view(parsed)
    assert "📌 Дополнительные поля" in rendered
    assert "customField" in rendered
    assert '"value"' in rendered


def test_pretty_mxcfg_view_handles_non_dict_step():
    parsed = {"steps": ["not-a-dict", {"d": 1}]}
    rendered = pretty_mxcfg_view(parsed)
    assert "Шаг 1" in rendered
    assert "Шаг 2" in rendered


def test_pretty_mxcfg_view_accepts_camelcase_afterdur():
    parsed = {"afterDur": 500}
    rendered = pretty_mxcfg_view(parsed)
    assert "500 мс" in rendered
