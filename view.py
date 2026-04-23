"""Human-readable rendering of MXCFG JSON objects for Telegram messages."""

from __future__ import annotations

from typing import Any

_TOP_LABELS: dict[str, str] = {
    "author": "👤 Автор",
    "description": "📝 Описание",
    "encrypted": "🔐 Шифрование",
    "AfterDur": "⏱ После таймера",
    "scriptMode": "⚙️ Режим скрипта",
    "data": "🌐 Имитация сети",
    "steps": "📋 Шаги",
    "onStop": "🛑 При остановке",
}

_DATA_LABELS: dict[str, str] = {
    "ZaderPC": "Задержка пакетов клиента",
    "DeletPC": "Удаление пакетов клиента",
    "ZaderPS": "Задержка пакетов сервера",
    "DeletPS": "Удаление пакетов сервера",
    "Auto": "Автоотключение",
}

_STEP_LABELS: dict[str, str] = {
    "d": "Задержка",
    "dp": "Удаление пакетов",
    "sd": "Задержка серверных пакетов",
    "sdp": "Удаление серверных пакетов",
    "dur": "Длительность",
    "drainC": "Пакеты клиента",
    "drainCD": "Задержка пакетов клиента",
    "drainS": "Пакеты сервера",
    "drainSD": "Задержка пакетов сервера",
    "szM": "Режим размера",
    "szMin": "Мин. размер",
    "szMax": "Макс. размер",
}

_KNOWN_TOP_KEYS: frozenset[str] = frozenset(
    {
        "author",
        "description",
        "encrypted",
        "AfterDur",
        "afterDur",
        "scriptMode",
        "data",
        "steps",
        "onStop",
    }
)


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_scalar(value: Any) -> str:
    """Render a scalar value for HTML-safe display."""
    if isinstance(value, bool):
        return "✅ включено" if value else "❌ выключено"
    if value is None:
        return "null"
    if isinstance(value, str):
        return f'"{_html_escape(value)}"'
    return str(value)


def pretty_mxcfg_view(parsed: dict) -> str:
    """Render a parsed MXCFG JSON object as a Telegram HTML message."""
    lines: list[str] = ["<b>📂 Содержимое MXCFG</b>", ""]

    def add(label: str, value: Any) -> None:
        lines.append(f"<b>{label}:</b> {render_scalar(value)}")

    for key in ("author", "description", "encrypted"):
        if key in parsed:
            add(_TOP_LABELS[key], parsed[key])

    after_key = next((k for k in ("AfterDur", "afterDur") if k in parsed), None)
    if after_key:
        add(_TOP_LABELS["AfterDur"], f"{parsed[after_key]} мс")

    if "scriptMode" in parsed:
        add(_TOP_LABELS["scriptMode"], parsed["scriptMode"])

    if "data" in parsed and isinstance(parsed["data"], dict):
        lines += ["", f"<b>{_TOP_LABELS['data']}</b>"]
        for k, v in parsed["data"].items():
            lines.append(f"  • {_DATA_LABELS.get(k, k)}: {render_scalar(v)}")

    if "steps" in parsed and isinstance(parsed["steps"], list):
        lines += ["", f"<b>{_TOP_LABELS['steps']}</b>"]
        for i, step in enumerate(parsed["steps"], 1):
            if not isinstance(step, dict):
                lines.append(f"  • Шаг {i}: {render_scalar(step)}")
                continue
            lines.append(f"  <b>Шаг {i}:</b>")
            for k, v in step.items():
                lines.append(f"    — {_STEP_LABELS.get(k, k)}: {render_scalar(v)}")

    if "onStop" in parsed:
        lines.append("")
        add(_TOP_LABELS["onStop"], parsed["onStop"])

    unknown = [k for k in parsed if k not in _KNOWN_TOP_KEYS]
    if unknown:
        lines += ["", "<b>📌 Дополнительные поля</b>"]
        for k in unknown:
            lines.append(f"  • {k}: {render_scalar(parsed[k])}")

    return "\n".join(lines).strip()
