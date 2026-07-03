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

SYSTEM_PROMPT = """Ты — редактор Telegram-канала про бизнес и криптовалюты.
Стиль: дерзкий, уверенный, без воды и канцелярита. Никаких фраз вроде
"в мире стремительно меняющихся технологий". Пиши как человек, а не как пресс-релиз.
Формат поста: 2-4 коротких предложения + 1 эмодзи в тему + короткий вывод/мнение в конце.
Не выдумывай цифры и факты, которых нет в исходном тексте."""

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


def rewrite_news(title: str, summary: str) -> str:
    completion = client.chat.completions.create(
        model="google/gemma-4-31b-it:free",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Заголовок: {title}\n\nТекст: {summary}\n\nНапиши пост для канала."},
        ],
        max_tokens=400,
    )
    return completion.choices[0].message.content.strip()


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
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