#!/usr/bin/env python3
"""
RSS vs. HTML kazıma ölçümü — BBC News

Aynı haber başlıklarını iki (opsiyonel üç) yoldan çeker ve şunları ölçer:
  - İndirme süresi (ms)
  - Ağdan geçen veri (sıkıştırılmış) ve açılmış gövde boyutu (KB)
  - Parse süresi (ms)
  - Kaç başlık bulunduğu (kırılganlık göstergesi)
  - Selenium ile: tarayıcının tüm sayfa için indirdiği toplam veri (JS, CSS, görseller dahil)

Kullanım:
    python benchmark.py --runs 5
    python benchmark.py --runs 5 --selenium
    python benchmark.py --runs 5 --out results/results.json
"""
import argparse
import gzip
import json
import statistics
import time
import warnings
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

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

HTML_URL = "https://www.bbc.com/news"
RSS_URL = "https://feeds.bbci.co.uk/news/rss.xml"

# BBC'nin şu anki başlık işaretlemesi. BBC bunu değiştirirse kazıyıcı 0 başlık döner.
HEADLINE_SELECTOR = '[data-testid="card-headline"]'
N_HEADLINES = 20
SAMPLE_OUTPUT_PATH = Path("results/sample_output.txt")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    # brotli'yi dışarıda bırakıyoruz ki ham baytları ek paket olmadan açabilelim
    "Accept-Encoding": "gzip, deflate",
}


# ---------------------------------------------------------------- yardımcılar
def kb(n: float) -> float:
    """Bayt değerini kilobayta çevirir."""
    return n / 1024


def fmt_size(n: float) -> str:
    """Bayt boyutunu okunabilir KB veya MB metnine dönüştürür."""
    return f"{n / (1024 * 1024):.2f} MB" if n >= 1024 * 1024 else f"{kb(n):.1f} KB"


def median(values: list[float]) -> float:
    """Listenin medyanını döndürür; boşsa NaN döner."""
    return statistics.median(values) if values else float("nan")


def decompress(raw: bytes, encoding: str | None) -> bytes:
    """Content-Encoding başlığına göre ham yanıt gövdesini açar."""
    enc = (encoding or "").lower()
    if enc == "gzip":
        return gzip.decompress(raw)
    if enc == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def fetch(url: str) -> dict[str, Any]:
    """Her ölçümde yeni bağlantı (DNS + TLS dahil) açarak URL'yi indirir."""
    with requests.Session() as s:
        t0 = time.perf_counter()
        r = s.get(url, headers=HEADERS, stream=True, timeout=30)
        r.raise_for_status()
        raw = r.raw.read(decode_content=False)
        seconds = time.perf_counter() - t0
        body = decompress(raw, r.headers.get("Content-Encoding"))
    return {
        "seconds": seconds,
        "transfer_bytes": len(raw),
        "body_bytes": len(body),
        "body": body,
    }


def dedupe(titles: list[str], limit: int) -> list[str]:
    """Boş olmayan başlıkları sırayı koruyarak tekilleştirir ve sınırlar."""
    return list(dict.fromkeys(t for t in titles if t))[:limit]


# ------------------------------------------------------------------ parserlar
def parse_html(body: bytes) -> tuple[list[str], float]:
    """HTML gövdesinden CSS seçici ile başlıkları ve parse süresini döndürür."""
    t0 = time.perf_counter()
    soup = BeautifulSoup(body, HTML_PARSER)
    titles = [el.get_text(strip=True) for el in soup.select(HEADLINE_SELECTOR)]
    titles = dedupe(titles, N_HEADLINES)
    return titles, time.perf_counter() - t0


def parse_rss(body: bytes) -> tuple[list[str], float]:
    """RSS XML gövdesinden item başlıklarını ve parse süresini döndürür."""
    t0 = time.perf_counter()
    root = xml_fromstring(body)
    titles = [(item.findtext("title") or "").strip() for item in root.iter("item")]
    titles = dedupe(titles, N_HEADLINES)
    return titles, time.perf_counter() - t0


# ------------------------------------------------------------------- ölçümler
def bench_requests(
    url: str,
    parser: Callable[[bytes], tuple[list[str], float]],
    runs: int,
    log: list[str],
) -> tuple[list[dict[str, float]], list[str]]:
    """Verilen URL ve parser ile belirtilen tur sayısı kadar ölçüm yapar."""
    rows: list[dict[str, float]] = []
    titles: list[str] = []
    for i in range(runs):
        f = fetch(url)
        titles, parse_s = parser(f["body"])
        row = {
            "download_ms": f["seconds"] * 1000,
            "parse_ms": parse_s * 1000,
            "total_ms": (f["seconds"] + parse_s) * 1000,
            "transfer_bytes": float(f["transfer_bytes"]),
            "body_bytes": float(f["body_bytes"]),
            "headlines": float(len(titles)),
        }
        rows.append(row)
        emit(
            f"  tur {i + 1}/{runs}: {row['total_ms']:.0f} ms, "
            f"{fmt_size(f['transfer_bytes'])} aktarım, {len(titles)} başlık",
            log,
        )
        time.sleep(1)  # sunucuya nazik ol
    return rows, titles


def bench_selenium(runs: int, log: list[str]) -> tuple[list[dict[str, float]], list[str]]:
    """Headless Chrome ile tüm sayfa yükünü ve başlık çekimini ölçer."""
    from selenium import webdriver
    from selenium.webdriver.common.by import By

    rows: list[dict[str, float]] = []
    titles: list[str] = []
    for i in range(runs):
        opts = webdriver.ChromeOptions()
        opts.add_argument("--headless=new")
        opts.add_argument(f"--user-agent={HEADERS['User-Agent']}")
        t_start = time.perf_counter()
        driver = webdriver.Chrome(options=opts)  # her turda yeni profil = soğuk önbellek
        startup_s = time.perf_counter() - t_start
        try:
            t0 = time.perf_counter()
            driver.get(HTML_URL)  # 'load' olayına kadar bekler
            load_s = time.perf_counter() - t0

            t1 = time.perf_counter()
            els = driver.find_elements(By.CSS_SELECTOR, HEADLINE_SELECTOR)
            titles = dedupe([e.text.strip() for e in els], N_HEADLINES)
            parse_s = time.perf_counter() - t1

            # Başka alan adından gelen ve Timing-Allow-Origin göndermeyen kaynaklarda
            # transferSize 0 döner; bu toplam bir alt sınırdır.
            stats = driver.execute_script("""
                const nav = performance.getEntriesByType('navigation')[0];
                const res = performance.getEntriesByType('resource');
                let transfer = nav.transferSize, decoded = nav.decodedBodySize;
                for (const r of res) { transfer += r.transferSize; decoded += r.decodedBodySize; }
                return {transfer, decoded, requests: res.length + 1};
            """)
        finally:
            driver.quit()
        row = {
            "browser_startup_ms": startup_s * 1000,
            "download_ms": load_s * 1000,
            "parse_ms": parse_s * 1000,
            "total_ms": (load_s + parse_s) * 1000,
            "transfer_bytes": float(stats["transfer"]),
            "body_bytes": float(stats["decoded"]),
            "requests": float(stats["requests"]),
            "headlines": float(len(titles)),
        }
        rows.append(row)
        emit(
            f"  tur {i + 1}/{runs}: {row['total_ms']:.0f} ms, "
            f"{fmt_size(stats['transfer'])} aktarım, {stats['requests']} istek, {len(titles)} başlık",
            log,
        )
        time.sleep(1)
    return rows, titles


def summarize(rows: list[dict[str, float]]) -> dict[str, float]:
    """Ölçüm turlarındaki her metriğin medyanını hesaplar."""
    keys = rows[0].keys()
    return {k: median([r[k] for r in rows]) for k in keys}


def emit(text: str, log: list[str]) -> None:
    """Metni hem terminale yazdırır hem de çıktı günlüğüne ekler."""
    print(text)
    log.append(text)


# ------------------------------------------------------------------------ ana
def main() -> None:
    """Ölçümü çalıştırır, sonuçları ekrana ve dosyalara yazar."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5, help="Her yöntem için tekrar sayısı")
    ap.add_argument("--selenium", action="store_true", help="Headless Chrome ölçümünü dahil et")
    ap.add_argument("--out", default="results/results.json", help="JSON çıktı dosya yolu")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    log: list[str] = []
    results: dict[str, Any] = {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "runs": args.runs,
        "html_parser": HTML_PARSER,
    }

    emit(f"\n[1] RSS  ({RSS_URL})", log)
    rss_rows, rss_titles = bench_requests(RSS_URL, parse_rss, args.runs, log)
    results["rss"] = summarize(rss_rows)

    emit(f"\n[2] HTML + BeautifulSoup  ({HTML_URL})", log)
    html_rows, html_titles = bench_requests(HTML_URL, parse_html, args.runs, log)
    results["html_bs4"] = summarize(html_rows)

    if args.selenium:
        emit("\n[3] Selenium (headless Chrome, tüm sayfa)", log)
        sel_rows, _ = bench_selenium(args.runs, log)
        results["selenium"] = summarize(sel_rows)

    overlap = len(set(rss_titles) & set(html_titles))
    results["overlap_rss_vs_html"] = overlap

    r, h = results["rss"], results["html_bs4"]
    lines = [
        f"\nÖlçüm zamanı: {results['measured_at']} | {args.runs} turun medyanı | parser: {HTML_PARSER}\n",
        "| Yöntem | Aktarılan veri | Açılmış boyut | İndirme | Parse | Toplam | Başlık |",
        "|---|---|---|---|---|---|---|",
        f"| RSS (XML) | {fmt_size(r['transfer_bytes'])} | {fmt_size(r['body_bytes'])} | "
        f"{r['download_ms']:.0f} ms | {r['parse_ms']:.1f} ms | {r['total_ms']:.0f} ms | {r['headlines']:.0f} |",
        f"| HTML + BeautifulSoup | {fmt_size(h['transfer_bytes'])} | {fmt_size(h['body_bytes'])} | "
        f"{h['download_ms']:.0f} ms | {h['parse_ms']:.1f} ms | {h['total_ms']:.0f} ms | {h['headlines']:.0f} |",
    ]
    if args.selenium:
        s = results["selenium"]
        lines.append(
            f"| Selenium (tüm sayfa) | ≥{fmt_size(s['transfer_bytes'])} | ≥{fmt_size(s['body_bytes'])} | "
            f"{s['download_ms']:.0f} ms | {s['parse_ms']:.1f} ms | {s['total_ms']:.0f} ms | {s['headlines']:.0f} |"
        )
    emit("\n".join(lines), log)

    emit(
        f"\nOranlar: HTML, RSS'e göre {h['transfer_bytes'] / r['transfer_bytes']:.1f}x daha fazla veri aktardı, "
        f"{h['total_ms'] / r['total_ms']:.1f}x daha uzun sürdü.",
        log,
    )
    if args.selenium:
        s = results["selenium"]
        emit(
            f"Selenium: {s['requests']:.0f} istek, tarayıcı açılışı ayrıca ~{s['browser_startup_ms']:.0f} ms.",
            log,
        )
    emit(f"RSS ile HTML'deki ilk {N_HEADLINES} başlıktan {overlap} tanesi birebir aynı.", log)
    if h["headlines"] == 0:
        emit(
            f"\n⚠️  HTML kazıyıcı 0 başlık buldu: '{HEADLINE_SELECTOR}' seçicisi artık tutmuyor. "
            "Kırılganlığın canlı örneği: yazına ekle!",
            log,
        )

    emit("\nRSS'ten ilk 5 başlık:", log)
    for t in rss_titles[:5]:
        emit(f"  - {t}", log)

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(f"\nHam sonuçlar {args.out} dosyasına yazıldı.", log)

    SAMPLE_OUTPUT_PATH.write_text("\n".join(log).lstrip("\n") + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
