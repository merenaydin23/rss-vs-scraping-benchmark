#!/usr/bin/env python3
"""
results/results.json dosyasını okuyup docs/images/results_chart.png grafiğini üretir.

Kullanım:
    python make_charts.py
    python make_charts.py --input results/results.json --output docs/images/results_chart.png
"""
import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt


def build_chart(data: dict, out_path: Path) -> None:
    """Üç yan yana yatay çubuk grafikten oluşan karşılaştırma görselini oluşturur."""
    rss = data["rss"]
    html = data["html_bs4"]

    panels = [
        ("Aktarılan veri (KB)", rss["transfer_bytes"] / 1024, html["transfer_bytes"] / 1024, "{:.1f} KB"),
        ("Parse süresi (ms)", rss["parse_ms"], html["parse_ms"], "{:.1f} ms"),
        ("Toplam süre (ms)", rss["total_ms"], html["total_ms"], "{:.0f} ms"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2), facecolor="white")
    fig.subplots_adjust(wspace=0.35, top=0.80, bottom=0.26, left=0.07, right=0.97)

    colors = ["#257de0", "#f47230"]
    labels = ["RSS", "HTML"]

    for ax, (title, rss_val, html_val, fmt) in zip(axes, panels):
        ax.set_facecolor("white")
        values = [rss_val, html_val]
        y_pos = [1, 0]

        bars = ax.barh(y_pos, values, height=0.48, color=colors, edgecolor="none")
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=11, fontweight="bold", color="#222222")
        ax.set_title(title, fontsize=12, fontweight="bold", pad=12, color="#111111")

        max_val = max(values) if max(values) > 0 else 1.0
        ax.set_xlim(0, max_val * 1.32)
        ax.xaxis.set_visible(False)
        for spine in ("top", "right", "bottom"):
            ax.spines[spine].set_visible(False)
        ax.spines["left"].set_color("#dddddd")

        for bar, val in zip(bars, values):
            ax.text(
                val + max_val * 0.03,
                bar.get_y() + bar.get_height() / 2,
                fmt.format(val),
                va="center",
                ha="left",
                fontsize=10,
                fontweight="bold",
                color="#222222",
            )

        ratio = (html_val / rss_val) if rss_val > 0 else 0.0
        ax.text(
            0.5,
            -0.18,
            f"HTML, RSS'in {ratio:.1f} katı",
            transform=ax.transAxes,
            ha="center",
            va="top",
            fontsize=10,
            color="#444444",
        )

    fig.suptitle(
        "BBC News: RSS vs. HTML Kazıma Karşılaştırması",
        fontsize=14,
        fontweight="bold",
        color="#111111",
        y=0.96,
    )

    measured_at = data.get("measured_at", "-")
    runs = data.get("runs", "-")
    parser = data.get("html_parser", "-")
    fig.text(
        0.5,
        0.04,
        f"Kaynak: BBC News | Ölçüm tarihi: {measured_at} | {runs} turun medyanı | Parser: {parser}",
        ha="center",
        va="bottom",
        fontsize=9,
        color="#666666",
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180, facecolor="white")
    plt.close(fig)


def main() -> None:
    """Komut satırı argümanlarını okur ve grafiği kaydeder."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="results/results.json", help="Girdi JSON dosyası")
    ap.add_argument("--output", default="docs/images/results_chart.png", help="Çıktı PNG dosyası")
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    data = json.loads(in_path.read_text(encoding="utf-8"))
    build_chart(data, out_path)
    print(f"Grafik oluşturuldu: {out_path}")


if __name__ == "__main__":
    main()
