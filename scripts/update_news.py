from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


OUTPUT_FILE = Path("news.json")
MAX_PER_CATEGORY = 5
MAX_ARTICLES = 25
MIN_ARTICLES = 5
MAX_AGE_DAYS = 10


def google_news_feed(query: str) -> str:
    params = urllib.parse.urlencode(
        {"q": f"{query} when:7d", "hl": "es", "gl": "ES", "ceid": "ES:es"}
    )
    return f"https://news.google.com/rss/search?{params}"


FEEDS = {
    "Mercado": google_news_feed(
        '"mercado inmobiliario" OR "precio de la vivienda" OR alquiler España'
    ),
    "Hipotecas": google_news_feed(
        'hipotecas OR euríbor OR "tipo de interés" vivienda España'
    ),
    "Inversión": google_news_feed(
        '"inversión inmobiliaria" OR "rentabilidad vivienda" OR "comprar para alquilar" España'
    ),
    "Reformas": google_news_feed(
        'reformas vivienda OR rehabilitación OR "eficiencia energética" edificios España'
    ),
    "Normativa": google_news_feed(
        '"ley de vivienda" OR normativa alquiler OR regulación inmobiliaria España'
    ),
}


CATEGORY_KEYWORDS = {
    "Mercado": ("vivienda", "inmobili", "alquiler", "compraventa", "casa", "piso", "construcción"),
    "Hipotecas": ("hipoteca", "euríbor", "interés", "préstamo", "banco"),
    "Inversión": ("invers", "rentabilidad", "alquilar", "socimi", "activo inmobiliario"),
    "Reformas": ("reforma", "rehabilita", "eficiencia energética", "obra", "construcción"),
    "Normativa": ("ley", "normativa", "regulación", "decreto", "impuesto", "fiscal", "alquiler"),
}


CATEGORY_IMAGES = {
    "Mercado": "https://images.unsplash.com/photo-1560518883-ce09059eeffa?auto=format&fit=crop&w=1400&q=80",
    "Hipotecas": "https://images.unsplash.com/photo-1564013799919-ab600027ffc6?auto=format&fit=crop&w=1400&q=80",
    "Inversión": "https://images.unsplash.com/photo-1554469384-e58fac16e23a?auto=format&fit=crop&w=1400&q=80",
    "Reformas": "https://images.unsplash.com/photo-1503387762-592deb58ef4e?auto=format&fit=crop&w=1400&q=80",
    "Normativa": "https://images.unsplash.com/photo-1450101499163-c8848c66ca85?auto=format&fit=crop&w=1400&q=80",
}


def clean_text(value: str | None) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_title(title: str, source: str) -> str:
    title = clean_text(title)
    suffix = f" - {source}"
    if source and title.lower().endswith(suffix.lower()):
        title = title[: -len(suffix)].strip()
    return title


def parse_date(value: str | None) -> dt.datetime:
    if value:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def useful_summary(description: str, title: str, source: str, category: str) -> str:
    summary = clean_text(description)
    for fragment in (title, source):
        if fragment:
            summary = summary.replace(fragment, " ")
    summary = re.sub(r"\s+", " ", summary).strip(" -·")
    if len(summary) < 80:
        return (
            f"Actualidad sobre {category.lower()} publicada por {source}. "
            "Consulta la noticia completa para conocer todos los detalles."
        )
    if len(summary) > 320:
        summary = summary[:320].rsplit(" ", 1)[0] + "…"
    return summary


def fetch_feed(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GreenMonkeyNewsBot/1.0 (+https://github.com/GreenMonkey-m2/greenmonkey-news)",
            "Accept": "application/rss+xml, application/xml, text/xml",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def parse_feed(category: str, data: bytes, now: dt.datetime) -> list[dict]:
    root = ET.fromstring(data)
    articles: list[dict] = []
    cutoff = now - dt.timedelta(days=MAX_AGE_DAYS)

    for item in root.findall(".//item"):
        source_node = item.find("source")
        source = clean_text(source_node.text if source_node is not None else "") or "Medio externo"
        title = clean_title(item.findtext("title") or "", source)
        link = clean_text(item.findtext("link"))
        published = parse_date(item.findtext("pubDate"))

        if not title or not link or published < cutoff:
            continue
        lowered = title.casefold()
        if not any(keyword.casefold() in lowered for keyword in CATEGORY_KEYWORDS[category]):
            continue

        stable_id = hashlib.sha256(f"{title.casefold()}|{source.casefold()}".encode()).hexdigest()[:16]
        articles.append(
            {
                "id": stable_id,
                "title": title,
                "summary": useful_summary(item.findtext("description") or "", title, source, category),
                "source": source,
                "category": category,
                "url": link,
                "image_url": CATEGORY_IMAGES[category],
                "published_at": published.isoformat().replace("+00:00", "Z"),
                "published_date": published.date().isoformat(),
                "featured": False,
            }
        )

    articles.sort(key=lambda article: article["published_at"], reverse=True)
    return articles[:MAX_PER_CATEGORY]


def main() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    collected: list[dict] = []
    seen_ids: set[str] = set()

    for category, feed_url in FEEDS.items():
        try:
            articles = parse_feed(category, fetch_feed(feed_url), now)
            print(f"{category}: {len(articles)} noticias válidas")
        except Exception as exc:
            print(f"{category}: error al consultar la fuente: {exc}")
            continue

        for article in articles:
            if article["id"] in seen_ids:
                continue
            seen_ids.add(article["id"])
            collected.append(article)

    collected.sort(key=lambda article: article["published_at"], reverse=True)
    collected = collected[:MAX_ARTICLES]

    if len(collected) < MIN_ARTICLES:
        if OUTPUT_FILE.exists():
            print("No hay suficientes noticias nuevas; se conserva el archivo anterior.")
            return
        raise RuntimeError("No se han obtenido suficientes noticias para crear el archivo inicial.")

    collected[0]["featured"] = True
    payload = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "count": len(collected),
        "categories": list(FEEDS),
        "articles": collected,
    }

    temporary = OUTPUT_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT_FILE)
    print(f"Archivo actualizado con {len(collected)} noticias.")


if __name__ == "__main__":
    main()
