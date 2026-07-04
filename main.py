"""
RSS -> рерайт через Gemma 4 (OpenRouter) -> публикация в Telegram.
Важные новости дополнительно иллюстрируются картинкой (OpenRouter Image API).

Запускается ОДИН РАЗ за вызов (расписание задаёт GitHub Actions, см. .github/workflows/post.yml).
Никаких локальных зависимостей вроде aiogram — только requests + openai SDK.

Установка (для локального теста, необязательно):
    pip install openai feedparser requests --break-system-packages
"""

import os
import json
import time
import feedparser
import requests
from openai import OpenAI

# ---- Конфиг: значения приходят из переменных окружения (GitHub Secrets) ----
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]  # например "@my_crypto_channel"
PIXAZO_API_KEY = os.environ["PIXAZO_API_KEY"]

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

IMPORTANCE_PROMPT = """Ты фильтруешь новости для крипто/бизнес-канала.
Ответь ТОЛЬКО одним словом: ВАЖНО или ОБЫЧНО.

ВАЖНО — если новость про:
- крупные регуляторные решения (законы, SEC, запреты/разрешения)
- резкие движения топовых активов (BTC, ETH, крупные индексы) на значимую величину
- вход/выход крупных институциональных игроков (банки, фонды, BlackRock и т.п.)
- крупные хаки, банкротства, скандалы с крупными игроками
- принципиально новые продукты/законы, которые меняют правила игры

ОБЫЧНО — если это рутинная новость: мелкий альткоин, локальная новость без
большого влияния, повторяющийся тип новости (очередной листинг, очередное мнение
аналитика без крупного повода)."""

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


def is_important_news(title: str, summary: str) -> bool:
    """Решает, тянет ли новость на картинку (важная) или нет (рутинная)."""
    try:
        completion = client.chat.completions.create(
            model="google/gemma-4-31b-it:free",
            messages=[
                {"role": "system", "content": IMPORTANCE_PROMPT},
                {"role": "user", "content": f"Заголовок: {title}\n\nТекст: {summary}"},
            ],
            max_tokens=10,
        )
        answer = completion.choices[0].message.content.strip().upper()
        return "ВАЖНО" in answer
    except Exception as e:
        print(f"Ошибка оценки важности: {e}")
        return False


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


def generate_image(title: str):
    """Генерирует картинку через Pixazo API (Flux Schnell). Возвращает URL картинки или None."""
    prompt = (
        f"Professional minimalist illustration for a crypto/business news post: "
        f"{title}. Modern, dark color palette with accent colors, no text on image, "
        f"no real company logos."
    )

    try:
        # 1. Отправляем запрос на генерацию
        submit_resp = requests.post(
            "https://gateway.pixazo.ai/flux-1-schnell/v1/getData",
            headers={
                "Content-Type": "application/json",
                "Ocp-Apim-Subscription-Key": PIXAZO_API_KEY,
            },
            json={
                "prompt": prompt,
                "num_steps": 4,
                "height": 1024,
                "width": 1024,
            },
            timeout=30,
        )
        submit_resp.raise_for_status()
        submit_data = submit_resp.json()

        request_id = submit_data.get("requestId") or submit_data.get("request_id")
        # некоторые ответы могут сразу содержать готовый результат
        direct_output = submit_data.get("output")
        if direct_output:
            return direct_output if isinstance(direct_output, str) else direct_output[0]

        if not request_id:
            print(f"Pixazo: не нашёл requestId в ответе: {submit_data}")
            return None

        # 2. Опрашиваем статус, пока не будет готово (максимум ~30 сек)
        for _ in range(15):
            time.sleep(2)
            status_resp = requests.post(
                "https://gateway.pixazo.ai/flux-1-schnell/v1/checkStatus",
                headers={
                    "Content-Type": "application/json",
                    "Ocp-Apim-Subscription-Key": PIXAZO_API_KEY,
                },
                json={"requestId": request_id},
                timeout=30,
            )
            status_resp.raise_for_status()
            status_data = status_resp.json()

            status = status_data.get("status", "").lower()
            if status == "completed":
                return status_data.get("output")
            if status in ("failed", "error"):
                print(f"Pixazo: генерация упала: {status_data}")
                return None

        print("Pixazo: не дождался результата за отведённое время")
        return None

    except Exception as e:
        print(f"Ошибка генерации картинки (Pixazo): {e}")
        return None


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


def send_photo_to_telegram(image_url: str, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    # у caption в Telegram лимит 1024 символа
    short_caption = caption if len(caption) <= 1024 else caption[:1021] + "..."
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHANNEL_ID,
        "photo": image_url,
        "caption": short_caption,
        "parse_mode": "Markdown",
    })
    if not resp.ok:
        print(f"Ошибка отправки фото ({resp.text}), фолбэк на обычный текст")
        send_to_telegram(caption)
        return
    resp.raise_for_status()


def main():
    seen = load_seen()
    posted_count = 0
    images_generated = 0

    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:5]:
            if entry.link in seen:
                continue
            seen.add(entry.link)

            title = entry.title
            summary = entry.get("summary", "")

            try:
                post_text = rewrite_news(title, summary)
            except Exception as e:
                print(f"Ошибка рерайта на '{title}': {e}")
                continue

            important = is_important_news(title, summary)
            image_url = generate_image(title) if important else None

            try:
                if image_url:
                    send_photo_to_telegram(image_url, post_text)
                    images_generated += 1
                    print(f"Опубликовано с картинкой: {title}")
                else:
                    send_to_telegram(post_text)
                    print(f"Опубликовано: {title}")
                posted_count += 1
            except Exception as e:
                print(f"Ошибка публикации '{title}': {e}")

    save_seen(seen)
    print(f"Готово. Опубликовано постов: {posted_count}, из них с картинкой: {images_generated}")


if __name__ == "__main__":
    main()