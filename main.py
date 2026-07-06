"""
Пайплайн:
1. RSS -> рерайт через Gemma 4 (OpenRouter) -> 2 новостных поста в Telegram (#Новости)
2. CoinGecko (реальные цены) -> анализ через Llama 3.3 70B -> 1 пост про рынок (#Рынок)
Важные новости дополнительно иллюстрируются картинкой (Pixazo Flux Schnell).

Запускается ОДИН РАЗ за вызов (расписание задаёт GitHub Actions).
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
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
PIXAZO_API_KEY = os.environ["PIXAZO_API_KEY"]
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY")  # опционально, есть keyless-фолбэк

SEEN_FILE = "seen_links.json"

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

FOOTER = "\n\n[Матвей | Крипта](https://t.me/KbusinessK)"
MARKET_DISCLAIMER = "Рынок никому ничего не должен. Любой прогноз — это лишь вероятность, а не гарантия."

# =========================================================
# ЧАСТЬ 1 — НОВОСТИ (Gemma)
# =========================================================

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

НЕ добавляй в конце голый список тикеров/названий через дефис — если нужно упомянуть
конкретные монеты/компании/законы, вплетай их прямо в предложения текста.
НЕ добавляй хештеги и не пиши что-либо про "не является рекомендацией" — это добавится отдельно.

ФОРМАТИРОВАНИЕ (Telegram Markdown):
- *текст* — жирный (ОДНА звёздочка с каждой стороны, не две)
- _текст_ — курсив (одно подчёркивание с каждой стороны)
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
большого влияния, повторяющийся тип новости."""

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
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-500:], f)


def normalize_markdown(text: str) -> str:
    text = text.replace("**", "*")
    text = text.replace("##", "").replace("###", "")
    return text


def is_important_news(title: str, summary: str) -> bool:
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
    finally:
        time.sleep(5)  # бесплатный тир OpenRouter: лимит 16 запросов/мин


def rewrite_news(title: str, summary: str) -> str:
    try:
        completion = client.chat.completions.create(
            model="google/gemma-4-31b-it:free",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Заголовок: {title}\n\nТекст: {summary}\n\nНапиши пост для канала."},
            ],
            max_tokens=600,
        )
        raw_text = completion.choices[0].message.content.strip()
        post = normalize_markdown(raw_text)
        return f"{post}\n\n#Новости{FOOTER}"
    finally:
        time.sleep(5)


def generate_image(title: str):
    """Генерирует картинку через Pixazo API (Flux Schnell). Возвращает URL картинки или None."""
    prompt = (
        f"Abstract conceptual illustration representing a crypto/business news topic: "
        f"{title}. Editorial art style, not cartoonish, not photorealistic — a stylized "
        f"balance between the two. Dark moody background with soft gradients and light "
        f"accents, abstract shapes suggesting growth/movement/networks rather than literal "
        f"icons like coins or banks. No text on image, no real company logos, no literal "
        f"bitcoin symbol."
    )
    try:
        submit_resp = requests.post(
            "https://gateway.pixazo.ai/flux-1-schnell/v1/getData",
            headers={
                "Content-Type": "application/json",
                "Ocp-Apim-Subscription-Key": PIXAZO_API_KEY,
            },
            json={"prompt": prompt, "num_steps": 4, "height": 1024, "width": 1024},
            timeout=30,
        )
        submit_resp.raise_for_status()
        submit_data = submit_resp.json()

        request_id = submit_data.get("requestId") or submit_data.get("request_id")
        direct_output = submit_data.get("output")
        if direct_output:
            return direct_output if isinstance(direct_output, str) else direct_output[0]

        if not request_id:
            print(f"Pixazo: не нашёл requestId в ответе: {submit_data}")
            return None

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


# =========================================================
# ЧАСТЬ 2 — АНАЛИЗ РЫНКА (Llama 3.3 70B + реальные данные CoinGecko)
# =========================================================

MARKET_ANALYSIS_PROMPT = """Ты — Матвей, автор крипто-канала. Тебе дали РЕАЛЬНЫЕ актуальные
рыночные данные (цены и % изменения за 24 часа). Напиши короткий пост с анализом рынка.

ПРАВИЛА (строго обязательны):
- Анализируй ТОЛЬКО те цифры, которые тебе дали. Не выдумывай других данных.
- Можешь называть направление (вверх/вниз) ТОЛЬКО если это прямо следует из данных
  (например, "+5%" за 24ч = уверенно можно сказать "вверх").
- Слово "резко" используй ТОЛЬКО если изменение действительно крупное (обычно от 5-7% и выше
  за 24 часа для основных монет). Для небольших движений (1-3%) пиши мягче: "подрастает",
  "слегка снижается", "в боковике" и т.п.
- Обязательно объясняй, ПОЧЕМУ ты делаешь такой вывод — просто ссылайся на конкретные цифры,
  которые тебе дали, а не абстрактные рассуждения.
- Тон уверенный, но без гарантий. Никаких "точно будет", "гарантированно вырастет".
- 3-5 предложений, простым языком, без канцелярита.
- Формат: эмодзи + *жирный* заголовок, затем текст.

ФОРМАТИРОВАНИЕ: *текст* для жирного (одна звёздочка), без HTML, без ##."""


def fetch_market_data():
    """Берёт реальные цены с CoinGecko (keyless public API, если ключа нет)."""
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": "bitcoin,ethereum,solana",
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    }
    if COINGECKO_API_KEY:
        params["x_cg_demo_api_key"] = COINGECKO_API_KEY

    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def analyze_market(market_data: dict) -> str:
    data_str = json.dumps(market_data, ensure_ascii=False)
    try:
        completion = client.chat.completions.create(
            model="meta-llama/llama-3.3-70b-instruct:free",
            messages=[
                {"role": "system", "content": MARKET_ANALYSIS_PROMPT},
                {"role": "user", "content": f"Реальные данные с рынка:\n{data_str}"},
            ],
            max_tokens=400,
        )
        raw_text = completion.choices[0].message.content.strip()
        post = normalize_markdown(raw_text)
        return f"{post}\n\n_{MARKET_DISCLAIMER}_\n\n#Рынок{FOOTER}"
    finally:
        time.sleep(5)


# =========================================================
# ОТПРАВКА В TELEGRAM
# =========================================================

def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown",
    })
    if not resp.ok:
        print(f"Markdown parse failed ({resp.text}), retrying as plain text")
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHANNEL_ID, "text": text})
    resp.raise_for_status()


def send_photo_to_telegram(image_url: str, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
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


# =========================================================
# MAIN
# =========================================================

def post_news(seen: set) -> tuple[int, int]:
    """Публикует до 2 новостных постов. Возвращает (постов, картинок)."""
    posted, images = 0, 0
    for feed_url in RSS_FEEDS:
        if posted >= 2:
            break
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:5]:
            if posted >= 2:
                break
            if entry.link in seen:
                continue
            seen.add(entry.link)

            title, summary = entry.title, entry.get("summary", "")
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
                    images += 1
                    print(f"Опубликовано с картинкой: {title}")
                else:
                    send_to_telegram(post_text)
                    print(f"Опубликовано: {title}")
                posted += 1
            except Exception as e:
                print(f"Ошибка публикации '{title}': {e}")
    return posted, images


def post_market_analysis():
    try:
        market_data = fetch_market_data()
        analysis = analyze_market(market_data)
        send_to_telegram(analysis)
        print("Опубликован анализ рынка")
        return True
    except Exception as e:
        print(f"Ошибка анализа рынка: {e}")
        return False


def main():
    seen = load_seen()

    news_posted, images_generated = post_news(seen)
    market_posted = post_market_analysis()

    save_seen(seen)
    print(
        f"Готово. Новостных постов: {news_posted} (с картинкой: {images_generated}), "
        f"пост про рынок: {'да' if market_posted else 'нет'}"
    )


if __name__ == "__main__":
    main()