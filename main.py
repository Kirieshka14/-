"""
Пайплайн:
1. RSS -> рерайт через Gemma 4 (OpenRouter) -> 2 новостных поста в Telegram (#Новости)
2. CoinGecko (реальные цены + реальные свечи BTC) -> анализ через Gemma -> 1 пост про рынок
   с графиком свечей и стрелкой направления (#Рынок)
3. Важные новости иллюстрируются реальным фото с Unsplash.
4. Публикация идёт НЕ через Bot API, а через userbot (Telethon), залогиненный под личным
   аккаунтом владельца канала. Это единственный способ реально отправлять премиум-эмодзи
   в канал: Bot API разрешает custom-emoji сущности от ботов только в приватных чатах,
   группах и супергруппах, но НЕ в каналах, даже если у владельца бота есть Premium.
"""

import os
import json
import re
import time
import io
import hashlib
import asyncio
import feedparser
import requests
from openai import OpenAI
from telethon import TelegramClient, types
from telethon.sessions import StringSession
from telethon.extensions import markdown as tl_markdown

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
TELEGRAM_API_ID = int(os.environ["TELEGRAM_API_ID"])
TELEGRAM_API_HASH = os.environ["TELEGRAM_API_HASH"]
TELETHON_SESSION = os.environ["TELETHON_SESSION"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
UNSPLASH_ACCESS_KEY = os.environ["UNSPLASH_ACCESS_KEY"]
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY")

SEEN_FILE = "seen_links.json"

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "http://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",
]

FOOTER = "\n\n[Матвей | Крипта](https://t.me/KbusinessK)"

# Премиум-эмодзи (владелец аккаунта — Telegram Premium; постим userbot'ом от его лица,
# поэтому custom-эмодзи реально рендерятся, в отличие от Bot API в каналах)
# формат: (базовый unicode-символ, custom_emoji_id)
PREMIUM_EMOJIS = [
    ("💸", "5231005931550030290"),
    ("💸", "5233326571099534068"),
    ("💸", "5231449120635370684"),
    ("💎", "5296742257146241213"),
    ("👛", "5424976816530014958"),
    ("💎", "5404558404565875143"),
    ("🍷", "6044096859054545852"),
    ("😎", "6044217848283274473"),
]

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "]",
    flags=re.UNICODE,
)

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
4. Если в новости есть термин, который не всем понятен (например ETF, DeFi, стейкинг,
   санкции, тарифы, ФРС и т.п.) — отдельным абзацем эмодзи + *жирное слово-термин* +
   короткое объяснение простыми словами
5. Пустая строка
6. Абзац с личным мнением/выводом Матвея — начинай с "На мой взгляд" или похожего,
   без воды, конкретная мысль

НЕ добавляй в конце голый список тикеров/названий через дефис — если нужно упомянуть
конкретные монеты/компании/законы, вплетай их прямо в предложения текста.
НЕ добавляй хештеги и не пиши что-либо про "не является рекомендацией" — это добавится отдельно.
НЕ подписывайся именем/названием канала и НЕ добавляй ссылку на канал — подпись добавляется
отдельно программно, ты её не пишешь вообще.

ФОРМАТИРОВАНИЕ (Telegram Markdown):
- *текст* — жирный (ОДНА звёздочка с каждой стороны, не две)
- _текст_ — курсив (одно подчёркивание с каждой стороны)
- НЕ используй ## решётки, HTML-теги, двойные звёздочки **

Не выдумывай цифры и факты, которых нет в исходном тексте — придумывать можно
только формулировку мнения/вывода, а не сами данные.

ВАЖНОСТЬ НОВОСТИ:
Дополнительно оцени, тянет ли новость на "ВАЖНО" (крупные регуляторные решения; резкие
движения топовых активов BTC/ETH на значимую величину; вход/выход крупных институциональных
игроков; крупные хаки/банкротства/скандалы; принципиально новые законы/продукты; крупные
геополитические события, способные повлиять на рынки — войны, санкции, заявления лидеров
стран, торговые конфликты, крупные политические решения) или "ОБЫЧНО" (рутинная новость,
мелкий альткоин, локальная новость без большого влияния, повторяющийся тип новости).

ФОРМАТ ОТВЕТА — строго валидный JSON, без markdown-обёртки в виде ```json, без пояснений:
{"importance": "ВАЖНО" или "ОБЫЧНО", "post": "текст поста здесь"}"""

MARKET_ANALYSIS_PROMPT = """Ты — Матвей, автор крипто-канала. Тебе дали РЕАЛЬНЫЕ актуальные
рыночные данные (цены и % изменения за 24 часа). Напиши пост с прогнозом — КУДА, по-твоему,
дальше пойдёт рынок: вверх, вниз или в боковик. Это не сводка новостей и не пересказ цифр —
это твоя оценка вероятного ближайшего направления.

ПРАВИЛА (строго обязательны):
- Обязательно назови направление явно: "рынок, скорее всего, пойдёт вверх/вниз" или
  "рынок, вероятно, останется в боковике" — это должно звучать как прогноз, а не как отчёт
  о том, что уже произошло.
- Прогноз строй ТОЛЬКО на основе моментума в данных, которые тебе дали (текущее направление
  движения за 24ч как сигнал вероятного продолжения). Не выдумывай других данных/новостей.
- Слово "резко" используй ТОЛЬКО если изменение за 24ч действительно крупное (обычно от 5-7%
  и выше для основных монет). Для небольших движений (1-3%) пиши мягче: "может продолжить
  плавный рост", "рискует немного просесть" и т.п.
- Обязательно объясняй ПОЧЕМУ ты делаешь такой прогноз — ссылаясь на конкретные цифры
  (например "раз BTC уже третий день в плюсе, моментум скорее в пользу продолжения роста").
- Тон уверенный, но без гарантий. Никаких "точно будет", "гарантированно вырастет" —
  это оценка вероятности, а не обещание.
- 3-5 предложений, простым языком, без канцелярита.
- Формат: эмодзи + *жирный* заголовок (заголовок тоже должен звучать как прогноз, например
  "Рынок готовится к развороту", а не "Рынок показывает рост").

ФОРМАТИРОВАНИЕ: *текст* для жирного (одна звёздочка), без HTML, без ##."""

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)

telethon_client = TelegramClient(StringSession(TELETHON_SESSION), TELEGRAM_API_ID, TELEGRAM_API_HASH)


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


def strip_signature(text: str) -> str:
    """Вырезает случайно добавленную моделью подпись/ссылку на канал."""
    text = re.sub(r"\[?\s*Матвей\s*\|\s*Крипта\s*\]?\s*\(?\s*https?://t\.me/KbusinessK\)?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://t\.me/KbusinessK", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Матвей\s*\|\s*Крипта", "", text, flags=re.IGNORECASE)
    return text.strip()


def build_telethon_markdown(text: str, seed: str) -> str:
    """Конвертирует наш внутренний Markdown (*bold*, _italic_, [текст](url)) в синтаксис,
    который понимает markdown-парсер Telethon (**bold**, __italic__), и подменяет заголовочный
    эмодзи на премиум-эмодзи через ссылку [emoji](emoji/ID) — это единственный синтаксис,
    которым Telethon умеет кодировать custom-emoji сущности."""
    text = re.sub(r"\*(.+?)\*", r"**\1**", text)
    text = re.sub(r"_(.+?)_", r"__\1__", text)
    match = EMOJI_PATTERN.search(text[:80])
    if match:
        idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(PREMIUM_EMOJIS)
        base_char, emoji_id = PREMIUM_EMOJIS[idx]
        text = text[: match.start()] + f"[{base_char}](emoji/{emoji_id})" + text[match.end() :]
    return text


def markdown_to_entities(text: str):
    """Парсит текст в (текст_без_разметки, [MessageEntity...]), подменяя ссылки вида
    emoji/ID на настоящие MessageEntityCustomEmoji."""
    parsed_text, entities = tl_markdown.parse(text)
    for i, e in enumerate(entities):
        if isinstance(e, types.MessageEntityTextUrl) and e.url.startswith("emoji/"):
            entities[i] = types.MessageEntityCustomEmoji(e.offset, e.length, int(e.url.split("/", 1)[1]))
    return parsed_text, entities


def _prepare_entities(md_text: str, seed: str, limit: int):
    tg_markdown = build_telethon_markdown(md_text, seed)
    text, entities = markdown_to_entities(tg_markdown)
    if len(text) <= limit:
        return text, entities
    # Текст длиннее лимита (например, длинная подпись к фото при лимите 1024) —
    # безопасно обрезаем: entities, которые вылезают за границу обрезки, отбрасываем,
    # чтобы не отправить в Telegram сущность с offset+length за пределами текста.
    cutoff = limit - 1
    truncated = text[:cutoff] + "…"
    kept_entities = [e for e in entities if e.offset + e.length <= cutoff]
    return truncated, kept_entities


def call_openrouter_with_retry(model: str, messages: list, max_tokens: int, retries: int = 3, wait_seconds: int = 40):
    last_error = None
    for attempt in range(retries + 1):
        try:
            completion = client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens)
            return completion.choices[0].message.content.strip()
        except Exception as e:
            last_error = e
            if "429" in str(e) and attempt < retries:
                print(f"Rate limit, жду {wait_seconds}с и пробую снова (попытка {attempt + 1}/{retries})...")
                time.sleep(wait_seconds)
                continue
            raise last_error
    raise last_error


def rewrite_and_classify(title: str, summary: str):
    """Один запрос: модель сразу пишет пост И оценивает важность новости."""
    raw_text = ""
    try:
        raw_text = call_openrouter_with_retry(
            model="google/gemma-4-31b-it:free",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Заголовок: {title}\n\nТекст: {summary}"},
            ],
            max_tokens=700,
        )
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()

        data = json.loads(cleaned)
        post = normalize_markdown(data.get("post", "").strip())
        post = strip_signature(post)
        important = "ВАЖНО" in data.get("importance", "").upper()
        full_post = f"{post}\n\n#Новости{FOOTER}"
        return full_post, important
    except json.JSONDecodeError as e:
        print(f"Не смог распарсить JSON от модели: {e}. Сырой ответ: {raw_text[:300]}")
        cleaned_fallback = strip_signature(normalize_markdown(raw_text))
        return f"{cleaned_fallback}\n\n#Новости{FOOTER}", False
    finally:
        time.sleep(5)


def search_unsplash_image(query: str):
    try:
        resp = requests.get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            params={"query": query, "per_page": 1, "orientation": "squarish"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if results:
            return results[0]["urls"]["regular"]
        return None
    except Exception as e:
        print(f"Ошибка поиска картинки (Unsplash): {e}")
        return None


def generate_image(title: str):
    query = title[:100]
    return search_unsplash_image(query)


def fetch_market_data():
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd", "include_24hr_change": "true"}
    if COINGECKO_API_KEY:
        params["x_cg_demo_api_key"] = COINGECKO_API_KEY
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def fetch_btc_ohlc(days: int = 1):
    """Реальные свечи BTC с CoinGecko: [[timestamp_ms, open, high, low, close], ...]"""
    url = "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc"
    params = {"vs_currency": "usd", "days": days}
    if COINGECKO_API_KEY:
        params["x_cg_demo_api_key"] = COINGECKO_API_KEY
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def analyze_market(market_data: dict) -> str:
    data_str = json.dumps(market_data, ensure_ascii=False)
    try:
        raw_text = call_openrouter_with_retry(
            model="google/gemma-4-31b-it:free",
            messages=[
                {"role": "system", "content": MARKET_ANALYSIS_PROMPT},
                {"role": "user", "content": f"Реальные данные с рынка:\n{data_str}"},
            ],
            max_tokens=400,
        )
        post = normalize_markdown(raw_text)
        post = strip_signature(post)
        return f"{post}\n\n#Рынок{FOOTER}"
    finally:
        time.sleep(5)


def generate_market_chart(avg_change: float) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches

    try:
        ohlc = fetch_btc_ohlc(days=1)
    except Exception as e:
        print(f"Не удалось получить OHLC-данные BTC: {e}")
        ohlc = []

    candles = ohlc[-8:] if len(ohlc) >= 3 else []

    bg_color = "#0d0f14"
    up_color = "#1fa855"
    down_color = "#e0393e"

    fig, ax = plt.subplots(figsize=(6, 7.5))
    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(bg_color)

    if candles:
        for i, (_, o, h, l, c) in enumerate(candles):
            color = up_color if c >= o else down_color
            ax.plot([i, i], [l, h], color=color, linewidth=2.5, zorder=2, solid_capstyle="round")
            body_bottom = min(o, c)
            body_height = max(abs(c - o), (h - l) * 0.03)
            rect = patches.Rectangle(
                (i - 0.28, body_bottom), 0.56, body_height, facecolor=color, edgecolor=color, zorder=3
            )
            ax.add_patch(rect)
        ax.set_xlim(-1, len(candles) + 0.5)
        lo = min(c[3] for c in candles)
        hi = max(c[2] for c in candles)
        pad = (hi - lo) * 0.25 if hi > lo else max(hi * 0.01, 1.0)
        ax.set_ylim(lo - pad, hi + pad * 2.4)

        arrow_color = up_color if avg_change >= 0 else down_color
        n = len(candles)
        x0, x1 = n * 0.55, n + 0.2
        ylo, yhi = ax.get_ylim()
        y_mid = (ylo + yhi) / 2
        if avg_change >= 0:
            y0, y1 = y_mid - (yhi - y_mid) * 0.3, yhi * 0.94
        else:
            y0, y1 = y_mid + (y_mid - ylo) * 0.3, ylo + (yhi - ylo) * 0.06
        ax.annotate(
            "",
            xy=(x1, y1),
            xytext=(x0, y0),
            arrowprops=dict(arrowstyle="-|>", color=arrow_color, lw=11, mutation_scale=42),
            zorder=5,
        )

    ax.axis("off")
    fig.text(0.5, 0.95, "КУДА ПОЙДЁТ РЫНОК?", color="white", fontsize=23, fontweight="bold", ha="center", family="sans-serif")

    plt.tight_layout(rect=[0, 0, 1, 0.9])
    path = "market_chart.png"
    plt.savefig(path, facecolor=fig.get_facecolor(), dpi=150)
    plt.close(fig)
    return path


async def send_to_telegram(md_text: str, seed: str):
    text, entities = _prepare_entities(md_text, seed, limit=4096)
    await telethon_client.send_message(TELEGRAM_CHANNEL_ID, text, formatting_entities=entities, parse_mode=None, link_preview=False)


async def send_photo_bytes_to_telegram(image_bytes: bytes, caption_md: str, seed: str):
    text, entities = _prepare_entities(caption_md, seed, limit=1024)
    file = io.BytesIO(image_bytes)
    file.name = "photo.jpg"
    try:
        await telethon_client.send_file(TELEGRAM_CHANNEL_ID, file, caption=text, formatting_entities=entities, parse_mode=None)
    except Exception as e:
        print(f"Ошибка отправки фото ({e}), фолбэк на обычный текст")
        await send_to_telegram(caption_md, seed)


async def send_photo_file_to_telegram(file_path: str, caption_md: str, seed: str):
    text, entities = _prepare_entities(caption_md, seed, limit=1024)
    try:
        await telethon_client.send_file(TELEGRAM_CHANNEL_ID, file_path, caption=text, formatting_entities=entities, parse_mode=None)
    except Exception as e:
        print(f"Ошибка отправки графика ({e}), фолбэк на обычный текст")
        await send_to_telegram(caption_md, seed)


async def post_news(seen: set):
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
                post_text, important = rewrite_and_classify(title, summary)
            except Exception as e:
                print(f"Ошибка рерайта на '{title}': {e}")
                continue

            image_url = generate_image(title) if important else None

            try:
                if image_url:
                    img_resp = requests.get(image_url, timeout=20)
                    img_resp.raise_for_status()
                    await send_photo_bytes_to_telegram(img_resp.content, post_text, seed=title)
                    images += 1
                    print(f"Опубликовано с картинкой: {title}")
                else:
                    await send_to_telegram(post_text, seed=title)
                    print(f"Опубликовано: {title}")
                posted += 1
            except Exception as e:
                print(f"Ошибка публикации '{title}': {e}")
    return posted, images


async def post_market_analysis():
    try:
        market_data = fetch_market_data()
        analysis = analyze_market(market_data)

        changes = [v.get("usd_24h_change", 0) for v in market_data.values() if isinstance(v, dict)]
        avg_change = sum(changes) / len(changes) if changes else 0

        chart_path = generate_market_chart(avg_change)
        await send_photo_file_to_telegram(chart_path, analysis, seed="market-" + analysis[:20])

        print("Опубликован анализ рынка")
        return True
    except Exception as e:
        print(f"Ошибка анализа рынка: {e}")
        return False


async def main():
    await telethon_client.connect()
    if not await telethon_client.is_user_authorized():
        raise RuntimeError(
            "Telethon-сессия не авторизована или истекла. "
            "Сгенерируй новую строку сессии через telethon_login.py и обнови секрет TELETHON_SESSION."
        )
    try:
        seen = load_seen()
        market_posted = await post_market_analysis()
        news_posted, images_generated = await post_news(seen)
        save_seen(seen)
        print(
            f"Готово. Новостных постов: {news_posted} (с картинкой: {images_generated}), "
            f"пост про рынок: {'да' if market_posted else 'нет'}"
        )
    finally:
        await telethon_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())