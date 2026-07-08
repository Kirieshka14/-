"""
Пайплайн:
1. RSS -> рерайт через Gemma 4 (OpenRouter) -> 2 новостных поста в Telegram (#Новости)
2. CoinGecko (реальные цены) -> анализ через Llama 3.3 70B -> 1 пост про рынок (#Рынок)
Важные новости иллюстрируются реальным фото с Unsplash (не AI-генерация).
"""

import os
import json
import re
import time
import feedparser
import requests
from openai import OpenAI

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
UNSPLASH_ACCESS_KEY = os.environ["UNSPLASH_ACCESS_KEY"]
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY")

SEEN_FILE = "seen_links.json"

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "http://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",  # CNBC World News
]

FOOTER = "\n\n[Матвей | Крипта](https://t.me/KbusinessK)"
MARKET_DISCLAIMER = "Рынок никому ничего не должен. Любой прогноз — это лишь вероятность, а не гарантия."

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
    """Вырезает случайно добавленную моделью подпись/ссылку на канал —
    футер добавляется отдельно программно, дублей быть не должно."""
    # markdown-ссылка вида [Матвей | Крипта](https://t.me/KbusinessK) в любом виде
    text = re.sub(r"\[?\s*Матвей\s*\|\s*Крипта\s*\]?\s*\(?\s*https?://t\.me/KbusinessK\)?", "", text, flags=re.IGNORECASE)
    # голая ссылка на канал без подписи
    text = re.sub(r"https?://t\.me/KbusinessK", "", text, flags=re.IGNORECASE)
    # просто текст подписи без ссылки
    text = re.sub(r"Матвей\s*\|\s*Крипта", "", text, flags=re.IGNORECASE)
    return text.strip()


def call_openrouter_with_retry(model: str, messages: list, max_tokens: int, retries: int = 3, wait_seconds: int = 40):
    """Вызов OpenRouter с повтором при rate limit (429)."""
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
    """Один запрос вместо двух: модель сразу пишет пост И оценивает важность новости."""
    try:
        raw_text = call_openrouter_with_retry(
            model="google/gemma-4-31b-it:free",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Заголовок: {title}\n\nТекст: {summary}"},
            ],
            max_tokens=700,
        )
        # на случай, если модель всё же обернёт в ```json ... ```
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
        # фолбэк: используем сырой текст как пост, важность считаем False
        cleaned_fallback = strip_signature(normalize_markdown(raw_text))
        return f"{cleaned_fallback}\n\n#Новости{FOOTER}", False
    finally:
        time.sleep(5)


def search_unsplash_image(query: str):
    """Ищет реальное фото по теме на Unsplash. Возвращает URL картинки или None."""
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
    """Подбирает реальное фото по теме новости через Unsplash."""
    # Берём короткий поисковый запрос — просто заголовок, Unsplash сам разберётся
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
        return f"{post}\n\n_{MARKET_DISCLAIMER}_\n\n#Рынок{FOOTER}"
    finally:
        time.sleep(5)


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TELEGRAM_CHANNEL_ID, "text": text, "parse_mode": "Markdown"})
    if not resp.ok:
        print(f"Markdown parse failed ({resp.text}), retrying as plain text")
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHANNEL_ID, "text": text})
    resp.raise_for_status()


def generate_market_chart(market_data: dict, avg_change: float) -> str:
    """Рисует картинку: зелёные/красные бары (реальные % изменения по монетам) + стрелка
    общего направления. Возвращает путь к сохранённому PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    coin_ids = ["bitcoin", "ethereum", "solana"]
    labels = ["BTC", "ETH", "SOL"]
    changes = [market_data.get(c, {}).get("usd_24h_change", 0) for c in coin_ids]

    bg_color = "#0d0f14"
    fig, ax = plt.subplots(figsize=(6, 6))
    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(bg_color)

    colors = ["#1fa855" if c >= 0 else "#e0393e" for c in changes]
    heights = [max(abs(c), 0.5) for c in changes]  # чисто визуальная высота, не абсолютная цена
    x = range(len(changes))
    ax.bar(x, heights, color=colors, width=0.5, zorder=3)

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, color="white", fontsize=16, fontweight="bold")
    ax.get_yaxis().set_visible(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_ylim(0, max(heights) * 1.8)

    arrow_color = "#1fa855" if avg_change >= 0 else "#e0393e"
    top = max(heights) * 1.7
    if avg_change >= 0:
        ax.annotate("", xy=(len(changes) - 0.2, top), xytext=(len(changes) - 1.3, top * 0.45),
                    arrowprops=dict(arrowstyle="-|>", color=arrow_color, lw=8, mutation_scale=35), zorder=4)
    else:
        ax.annotate("", xy=(len(changes) - 0.2, top * 0.15), xytext=(len(changes) - 1.3, top * 0.85),
                    arrowprops=dict(arrowstyle="-|>", color=arrow_color, lw=8, mutation_scale=35), zorder=4)

    plt.tight_layout()
    path = "market_chart.png"
    plt.savefig(path, facecolor=fig.get_facecolor(), dpi=150)
    plt.close(fig)
    return path


def send_photo_file_to_telegram(file_path: str, caption: str):
    """Отправляет локальный файл (не URL) как фото в Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    short_caption = caption if len(caption) <= 1024 else caption[:1021] + "..."
    with open(file_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": short_caption, "parse_mode": "Markdown"},
            files={"photo": f},
        )
    if not resp.ok:
        print(f"Ошибка отправки графика ({resp.text}), фолбэк на обычный текст")
        send_to_telegram(caption)
        return
    resp.raise_for_status()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    short_caption = caption if len(caption) <= 1024 else caption[:1021] + "..."
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHANNEL_ID, "photo": image_url,
        "caption": short_caption, "parse_mode": "Markdown",
    })
    if not resp.ok:
        print(f"Ошибка отправки фото ({resp.text}), фолбэк на обычный текст")
        send_to_telegram(caption)
        return
    resp.raise_for_status()


def post_news(seen: set):
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

        changes = [v.get("usd_24h_change", 0) for v in market_data.values() if isinstance(v, dict)]
        avg_change = sum(changes) / len(changes) if changes else 0

        chart_path = generate_market_chart(market_data, avg_change)
        send_photo_file_to_telegram(chart_path, analysis)

        print("Опубликован анализ рынка")
        return True
    except Exception as e:
        print(f"Ошибка анализа рынка: {e}")
        return False


def main():
    seen = load_seen()
    market_posted = post_market_analysis()
    news_posted, images_generated = post_news(seen)
    save_seen(seen)
    print(
        f"Готово. Новостных постов: {news_posted} (с картинкой: {images_generated}), "
        f"пост про рынок: {'да' if market_posted else 'нет'}"
    )


if __name__ == "__main__":
    main()