"""
What the model is told, and the shape of what it must send back.

``PROMPT_VERSION`` is stored with every answer. When the instructions are
improved, bump it: old answers keep working and it stays visible which wording
produced them.
"""

from __future__ import annotations

from api.modules.aiassist.internal._sanitize import ALLOWED_CLASSES

PROMPT_VERSION = 1

#: Reasoning effort requested for every check.
EFFORT = "high"

#: How much of the picture the model sees. ``high`` reads the small print of a
#: 7000px-tall question as well as ``original`` did in a test, at about a tenth
#: of the tokens (about 1.1k instead of 11.8k for the tallest question).
IMAGE_DETAIL = "high"

USER_TEXT = "Проверь ответ на этот вопрос."

INSTRUCTIONS = f"""Ты — эксперт по сертификационному экзамену Microsoft AZ-700 \
(Designing and Implementing Microsoft Azure Networking Solutions).

На картинке один экзаменационный вопрос: с вариантами A–D, HOTSPOT, drag-and-drop, \
упорядочивание шагов или часть кейса. Прочитай его внимательно и ОПРЕДЕЛИ ПРАВИЛЬНЫЙ \
ОТВЕТ САМОСТОЯТЕЛЬНО, опираясь на документацию Azure, а не на догадки о «ожидаемом» ответе.

Верни JSON строго по схеме, три поля:
1. "summary" — одна строка (до 140 символов, по-русски): о чём вопрос и какой сценарий. \
Без номера вопроса и без ответа.
2. "answer" — короткий итог. Для вопроса с вариантами — буквы («B» или «A, D»); для HOTSPOT \
и drag-and-drop — пары «пункт → выбор» через «; ». До 120 символов.
3. "html" — разбор в виде HTML-фрагмента: только содержимое <body>, без <html>, <head>, \
<style>, <script>, без внешних ссылок и картинок.

Как оформлять "html":
- Язык русский; технические термины, названия сервисов и SKU оставляй по-английски.
- Начни с блока <div class="verdict">…</div> — правильный ответ одной-двумя строками, с эмодзи ✅.
- Затем разбор: для вопроса с вариантами каждый вариант — <div class="opt right"> или \
<div class="opt wrong"> с эмодзи ✅/❌, буквой и одним-двумя предложениями «почему». Для HOTSPOT и \
drag-and-drop — таблица <table> «пункт — выбор — почему».
- При необходимости: <div class="tip">💡 …</div> (ключевая идея, которую стоит запомнить) и \
<div class="note">📘 …</div> (где посмотреть: название раздела документации Azure, без ссылок).
- Если вопрос нечитаем, данных не хватает или ответ спорный — скажи честно в \
<div class="warn">⚠️ …</div>; ничего не выдумывай.
- Небольшую схему можно нарисовать встроенным <svg> (блоки, стрелки, подписи), но только если \
она действительно проясняет ответ: теги <svg>, <g>, <path>, <rect>, <circle>, <ellipse>, <line>, \
<polyline>, <polygon>, <text>; без стилей, ссылок и скриптов; размер — через viewBox.
- Допустимые теги: p, ul, ol, li, strong, em, b, i, code, pre, br, h3, h4, table, thead, tbody, \
tr, th, td, div, span и перечисленные выше svg. Допустимые классы: {", ".join(ALLOWED_CLASSES)}.
- Будь краток: весь разбор читается за минуту.

В источниках с экзаменационными дампами правильные ответы нередко указаны неверно — отвечай так, \
как правильно по документации Azure."""

#: JSON Schema of the answer, sent as a strict structured output.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "answer": {"type": "string"},
        "html": {"type": "string"},
    },
    "required": ["summary", "answer", "html"],
    "additionalProperties": False,
}

#: Name of the structured output, as the service wants it.
RESPONSE_SCHEMA_NAME = "ai_answer"

#: Ceiling on what the model may produce, reasoning tokens included.
MAX_OUTPUT_TOKENS = 12000
