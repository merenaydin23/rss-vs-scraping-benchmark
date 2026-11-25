#!/usr/bin/env python3
"""
BBC News RSS ve ana sayfa haberlerini başlık + temiz link olarak listeler
ve iki kaynak arasındaki ortak haberleri linke göre karşılaştırır.

Kullanım:
    python headlines.py
    python headlines.py --limit 20
"""
import argparse
import html
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from defusedxml.ElementTree import fromstring as xml_fromstring
except ImportError:
    from xml.etree.ElementTree import fromstring as xml_fromstring

    warnings.warn(
        "defusedxml kurulu değil; standart xml.etree.ElementTree kullanılıyor.",
        RuntimeWarning,
        stacklevel=1,
    )

try:
    import lxml  # noqa: F401

    HTML_PARSER = "lxml"
except ImportError:
    HTML_PARSER = "html.parser"

RSS_URL = "https://feeds.bbci.co.uk/news/rss.xml"
HTML_URL = "https://www.bbc.com/news"
HEADLINE_SELECTOR = '[data-testid="card-headline"]'

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
}


def clean_http_url(raw_url: str, base_url: str = "") -> str | None:
    """Linkin sorgu parametrelerini temizler ve sadece http/https şemasını kabul eder."""
    if not raw_url:
        return None
    full_url = urljoin(base_url, raw_url.strip()) if base_url else raw_url.strip()
    cleaned = full_url.split("?")[0].rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return cleaned


def fetch_rss_items(limit: int) -> list[dict[str, str]]:
    """RSS beslemesinden başlık, link, yayın tarihi ve özet bilgilerini çeker."""
    resp = requests.get(RSS_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    root = xml_fromstring(resp.content)

    items: list[dict[str, str]] = []
    seen_links: set[str] = set()
    for item in root.iter("item"):
        link = clean_http_url(item.findtext("link") or "")
        title = (item.findtext("title") or "").strip()
        if not link or not title or link in seen_links:
            continue
        seen_links.add(link)
        items.append({
            "title": title,
            "link": link,
            "published": (item.findtext("pubDate") or "").strip(),
            "summary": (item.findtext("description") or "").strip(),
        })
        if len(items) >= limit:
            break
    return items


def fetch_html_items(limit: int) -> list[dict[str, str]]:
    """BBC News ana sayfasından başlık ve link bilgilerini çeker."""
    resp = requests.get(HTML_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, HTML_PARSER)

    items: list[dict[str, str]] = []
    seen_links: set[str] = set()
    for el in soup.select(HEADLINE_SELECTOR):
        title = el.get_text(strip=True)
        anchor = el.find_parent("a") or el.find("a")
        href = anchor.get("href") if anchor else None
        link = clean_http_url(href or "", base_url="https://www.bbc.com")
        if not link or not title or link in seen_links:
            continue
        seen_links.add(link)
        items.append({"title": title, "link": link})
        if len(items) >= limit:
            break
    return items


def link_key(url: str) -> str:
    """Karşılaştırma için bbc.co.uk / bbc.com alan adı farkını normalize eder."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host in ("bbc.co.uk", "www.bbc.co.uk", "bbc.com", "www.bbc.com"):
        host = "www.bbc.com"
    return f"{host}{parsed.path}"


def render_html_report(
    rss_items: list[dict[str, str]],
    html_items: list[dict[str, str]],
    common_keys: set[str],
    out_path: Path,
) -> None:
    """Tüm metinleri html.escape ile temizleyerek isteğe bağlı HTML raporu üretir."""
    def li_rows(rows: list[dict[str, str]], show_meta: bool = False) -> str:
        parts = []
        for r in rows:
            t = html.escape(r["title"])
            u = html.escape(r["link"], quote=True)
            mark = " <strong>[ortak]</strong>" if link_key(r["link"]) in common_keys else ""
            meta = ""
            if show_meta:
                pub = html.escape(r.get("published", ""))
                summ = html.escape(r.get("summary", ""))
                meta = f"<br><small>{pub}</small><p>{summ}</p>"
            parts.append(f'<li><a href="{u}">{t}</a>{mark}{meta}</li>')
        return "\n".join(parts)

    doc = (
        "<!doctype html><html lang='tr'><head><meta charset='utf-8'>"
        "<title>BBC News: RSS vs Ana Sayfa</title></head><body>"
        f"<h1>Ortak haber sayısı: {len(common_keys)}</h1>"
        f"<h2>RSS ({len(rss_items)})</h2><ol>{li_rows(rss_items, show_meta=True)}</ol>"
        f"<h2>Ana Sayfa ({len(html_items)})</h2><ol>{li_rows(html_items)}</ol>"
        "</body></html>"
    )
    out_path.write_text(doc, encoding="utf-8")


def main() -> None:
    """RSS ve ana sayfa haberlerini listeler ve link bazlı kesişimi gösterir."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="Kaynak başına azami haber sayısı")
    ap.add_argument("--html-out", default="", help="İsteğe bağlı HTML çıktı dosyası")
    args = ap.parse_args()

    rss_items = fetch_rss_items(args.limit)
    html_items = fetch_html_items(args.limit)

    print(f"\n[RSS] {len(rss_items)} haber:")
    for idx, item in enumerate(rss_items, 1):
        print(f"  {idx:2d}. {item['title']}")
        print(f"      Link: {item['link']}")
        if item["published"]:
            print(f"      Tarih: {item['published']}")
        if item["summary"]:
            print(f"      Özet: {item['summary']}")

    print(f"\n[HTML Ana Sayfa] {len(html_items)} haber:")
    for idx, item in enumerate(html_items, 1):
        print(f"  {idx:2d}. {item['title']}")
        print(f"      Link: {item['link']}")

    rss_by_key = {link_key(item["link"]): item for item in rss_items}
    html_keys = {link_key(item["link"]) for item in html_items}
    common_keys = sorted(set(rss_by_key) & html_keys)

    print(f"\nOrtak haber sayısı (linke göre): {len(common_keys)}")
    for idx, key in enumerate(common_keys, 1):
        item = rss_by_key[key]
        print(f"  {idx:2d}. {item['title']} -> {item['link']}")

    if args.html_out:
        out_path = Path(args.html_out)
        render_html_report(rss_items, html_items, set(common_keys), out_path)
        print(f"\nHTML raporu yazıldı: {out_path}")


if __name__ == "__main__":
    main()
