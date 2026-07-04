"""
RSS -> рерайт через Gemma 4 (OpenRouter) -> публикация в Telegram.

Запускается ОДИН РАЗ за вызов (расписание задаёт GitHub Actions, см. .github/workflows/post.yml).
Никаких локальных зависимостей вроде aiogram — только requests + openai SDK.

Установка (для локального теста, необязательно):
    pip install openai feedparser requests --break-system-packages
"""

import os
import json
import feedparser
import requests
from openai import OpenAI

# ---- Конфиг: значения приходят из переменных окружения (GitHub Secrets) ----
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]  # например "@my_crypto_channel"

SEEN_FILE = "seen_links.json"  # хранит уже опубликованные ссылки между запусками

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

SYSTEM_PROMPT = """Ты пишешь посты от лица Матвея — автора Telegram-канала про бизнес,
криптовалюты, инвестиции и финансовую грамотность.

КТО ОН:
Матвей практикуется в бизнесе, крипте, инвестициях. Делится личным опытом,
наблюдениями и аналитикой. Его позиция: канал не про "быстрые иксы" и не про
гарантии. Это его личное мнение о том, как разумно подходить к деньгам, рискам,
рынку и созданию капитала.

СТИЛЬ:
- Уверенный, дерзкий, но не инфоцыганский — без обещаний лёгких денег
- Пишет как человек с своим мнением, а не как новостная сводка
- Без канцелярита и клише вроде "в мире стремительно меняющихся технологий"

СТРУКТУРА ПОСТА (обязательно следуй этому шаблону):

1. Заголовок: эмодзи + *жирный* короткий цепляющий заголовок сути новости (одна строка)
2. Пустая строка
3. Абзац: 2-3 предложения — что произошло, простыми словами, как будто объясняешь другу
4. Если в новости есть термин, который не всем понятен (например ETF, DeFi, стейкинг) —
   отдельным абзацем эмодзи + *жирное слово-термин* + короткое объяснение простыми словами
5. Пустая строка
6. Абзац с личным мнением/выводом Матвея — начинай с "На мой взгляд" или похожего,
   без воды, конкретная мысль
7. Если уместно — список конкретики через дефис (например названия монет, компаний,
   тикеров, законов), 2-4 пункта
8. Если пост даёт инвестиционную наводку (акции, фонды, конкретные монеты) —
   последней строкой курсивом (одно подчёркивание с двух сторон): _Не является
   индивидуальной инвестиционной рекомендацией_

ФОРМАТИРОВАНИЕ (Telegram Markdown):
- *текст* — жирный (ОДНА звёздочка с каждой стороны, не две)
- _текст_ — курсив (одно подчёркивание с каждой стороны)
- Дефис "- " в начале строки — маркированный список
- НЕ используй ## решётки, HTML-теги, двойные звёздочки **

Не выдумывай цифры и факты, которых нет в исходном тексте — придумывать можно
только формулировку мнения/вывода, а не сами данные."""

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    # храним только последние 500, чтобы файл не рос бесконечно
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-500:], f)


def normalize_markdown(text: str) -> str:
    """На случай, если модель всё же выдаст двойные звёздочки — приводим к формату
    Telegram legacy Markdown (одна звёздочка = жирный)."""
    text = text.replace("**", "*")
    text = text.replace("##", "").replace("###", "")
    return text


def rewrite_news(title: str, summary: str) -> str:
    completion = client.chat.completions.create(
        model="google/gemma-4-31b-it:free",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Заголовок: {title}\n\nТекст: {summary}\n\nНапиши пост для канала."},
        ],
        max_tokens=600,
    )
    raw_text = completion.choices[0].message.content.strip()
    return normalize_markdown(raw_text)


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown",
    })
    if not resp.ok:
        # если Markdown невалиден (модель накосячила с символами) — фолбэк без форматирования
        print(f"Markdown parse failed ({resp.text}), retrying as plain text")
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHANNEL_ID, "text": text})
    resp.raise_for_status()


def main():
    seen = load_seen()
    posted_count = 0

    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:5]:
            if entry.link in seen:
                continue
            seen.add(entry.link)

            try:
                post_text = rewrite_news(entry.title, entry.get("summary", ""))
                send_to_telegram(post_text)
                posted_count += 1
                print(f"Опубликовано: {entry.title}")
            except Exception as e:
                print(f"Ошибка на '{entry.title}': {e}")

    save_seen(seen)
    print(f"Готово. Опубликовано новых постов: {posted_count}")


if __name__ == "__main__":
    main()