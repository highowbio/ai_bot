"""HTML rendering helpers for parsed MXCFG dictionaries."""

from __future__ import annotations

import html
from typing import Any

DIVIDER = "━━━━━━━━━━━━━━━━━━"


def _esc(v: Any) -> str:
    return html.escape(str(v), quote=False)


def _render_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "✅ включено" if value else "❌ выключено"
    if value is None:
        return "—"
    if isinstance(value, str):
        return f'"{_esc(value)}"'
    if isinstance(value, (int, float)):
        return _esc(value)
    return _esc(value)


def pretty_mxcfg_view(parsed: dict) -> str:
    top_labels = {
        "author":      "👤 Автор",
        "description": "📝 Описание",
        "encrypted":   "🔐 Шифрование",
        "AfterDur":    "⏱ После таймера",
        "scriptMode":  "⚙️ Режим скрипта",
        "data":        "🌐 Имитация сети",
        "steps":       "📋 Шаги",
        "onStop":      "🛑 При остановке",
    }
    data_labels = {
        "ZaderPC": "Задержка пакетов клиента",
        "DeletPC": "Удаление пакетов клиента",
        "ZaderPS": "Задержка пакетов сервера",
        "DeletPS": "Удаление пакетов сервера",
        "Auto":    "Автоотключение",
    }
    step_labels = {
        "d":       "Задержка",
        "dp":      "Удаление пакетов",
        "sd":      "Задержка серверных пакетов",
        "sdp":     "Удаление серверных пакетов",
        "dur":     "Длительность",
        "drainC":  "Пакеты клиента",
        "drainCD": "Задержка пакетов клиента",
        "drainS":  "Пакеты сервера",
        "drainSD": "Задержка пакетов сервера",
        "szM":     "Режим размера",
        "szMin":   "Мин. размер",
        "szMax":   "Макс. размер",
    }

    lines: list[str] = [
        "<div class='mx-header'><b>📂 Содержимое MXCFG</b></div>",
        "<hr/>",
    ]

    def add(label: str, value: Any) -> None:
        lines.append(f"<div class='mx-row'><b>{label}:</b> {_render_scalar(value)}</div>")

    for key in ("author", "description", "encrypted"):
        if key in parsed:
            add(top_labels[key], parsed[key])

    after_key = next((k for k in ("AfterDur", "afterDur") if k in parsed), None)
    if after_key is not None:
        lines.append(
            f"<div class='mx-row'><b>{top_labels['AfterDur']}:</b> "
            f"{_esc(parsed[after_key])} мс</div>"
        )

    if "scriptMode" in parsed:
        add(top_labels["scriptMode"], parsed["scriptMode"])

    if isinstance(parsed.get("data"), dict):
        lines.append(f"<div class='mx-section'><b>{top_labels['data']}</b></div>")
        for k, v in parsed["data"].items():
            lines.append(
                f"<div class='mx-row sub'>• {_esc(data_labels.get(k, k))}: "
                f"{_render_scalar(v)}</div>"
            )

    if isinstance(parsed.get("steps"), list):
        total = len(parsed["steps"])
        lines.append(
            f"<div class='mx-section'><b>{top_labels['steps']}</b> "
            f"<span class='mx-hint'>(всего: {total})</span></div>"
        )
        for i, step in enumerate(parsed["steps"], 1):
            if not isinstance(step, dict):
                lines.append(
                    f"<div class='mx-row sub'>• Шаг {i}: {_render_scalar(step)}</div>"
                )
                continue
            lines.append(f"<div class='mx-step'><b>Шаг {i}:</b></div>")
            for k, v in step.items():
                lines.append(
                    f"<div class='mx-row sub sub2'>— "
                    f"{_esc(step_labels.get(k, k))}: {_render_scalar(v)}</div>"
                )

    if "onStop" in parsed:
        add(top_labels["onStop"], parsed["onStop"])

    known = {
        "author", "description", "encrypted", "AfterDur", "afterDur",
        "scriptMode", "data", "steps", "onStop",
    }
    unknown = [k for k in parsed if k not in known]
    if unknown:
        lines.append("<div class='mx-section'><b>📌 Дополнительные поля</b></div>")
        for k in unknown:
            lines.append(
                f"<div class='mx-row sub'>• {_esc(k)}: {_render_scalar(parsed[k])}</div>"
            )

    return "\n".join(lines)
