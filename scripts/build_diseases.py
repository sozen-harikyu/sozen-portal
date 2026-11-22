#!/usr/bin/env python3
import csv
import html
import io
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DISEASE_DIR = BASE_DIR / "disease"
IMAGES_DIR = BASE_DIR / "images" / "diseases"

SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "").strip()
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://sozen-harikyu.github.io/sozen-portal").rstrip("/")
PORTAL_URL = os.environ.get("PORTAL_URL", "https://sozen-harikyu.github.io/sozen-portal/").strip()
ALLOW_INDEXING = os.environ.get("ALLOW_INDEXING", "false").lower() == "true"
INCLUDE_DRAFTS = os.environ.get("INCLUDE_DRAFTS", "true").lower() == "true"


def fetch_csv_text(source: str) -> str:
    if os.path.exists(source):
        return Path(source).read_text(encoding="utf-8-sig")
    req = urllib.request.Request(source, headers={"User-Agent": "sozen-portal-builder/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8-sig")


def sanitize_slug(slug: str, fallback_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", (slug or "").strip())
    return cleaned or f"disease-{fallback_id.lower()}"


def parse_diseases(csv_text: str) -> list[dict]:
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    if len(rows) < 2:
        return []
    headers = [h.strip() for h in rows[0]]

    def find_col(keyword: str, fallback: int) -> int:
        for i, h in enumerate(headers):
            if keyword in h:
                return i
        return fallback

    cols = {
        "id": find_col("ID", 0),
        "name": find_col("疾患名", 1),
        "category": find_col("カテゴリ", 2),
        "keywords": find_col("検索キーワード", 3),
        "slug": find_col("slug", 4),
        "meta_description": find_col("meta_description", 5),
        "summary": find_col("一言要約", 6),
        "symptoms": find_col("症状チェック", 7),
        "mechanism": find_col("原因", 8),
        "approach": find_col("施術アプローチ", 9),
        "faq": find_col("よくある質問", 10),
        "image_file": find_col("画像ファイル名", 11),
        "source_url": find_col("参考URL", 12),
        "status": find_col("公開ステータス", 13),
    }

    diseases = []
    for idx, row in enumerate(rows[1:], start=1):
        def get(key: str) -> str:
            c = cols[key]
            return row[c].strip() if c < len(row) else ""

        name = get("name")
        if not name:
            continue
        status = get("status") or "下書き"
        if "下書き" in status and not INCLUDE_DRAFTS:
            continue

        item_id = get("id") or f"D{idx:03d}"
        slug = sanitize_slug(get("slug"), item_id)
        image_file = get("image_file")
        image_exists = bool(image_file and (IMAGES_DIR / image_file).exists())

        diseases.append({
            "id": item_id,
            "name": name,
            "category": get("category") or "その他",
            "keywords": get("keywords"),
            "slug": slug,
            "meta_description": get("meta_description") or get("summary"),
            "summary": get("summary"),
            "symptoms": get("symptoms"),
            "mechanism": get("mechanism"),
            "approach": get("approach"),
            "faq": get("faq"),
            "image_file": image_file,
            "image_exists": image_exists,
            "source_url": get("source_url"),
            "status": status,
        })
    return diseases


def split_items(text: str) -> list[str]:
    return [re.sub(r"^・\s*", "", part).strip() for part in (text or "").split("/") if part.strip()]


def parse_faq(text: str) -> list[dict]:
    items = []
    for pair in split_items(text):
        parts = re.split(r"\s+A\.\s*", pair, maxsplit=1)
        q = re.sub(r"^Q\.\s*", "", parts[0]).strip()
        a = parts[1].strip() if len(parts) > 1 else ""
        if q:
            items.append({"q": q, "a": a})
    return items


def robots_meta_tag() -> str:
    return '<meta name="robots" content="index, follow" />' if ALLOW_INDEXING else '<meta name="robots" content="noindex, nofollow" />'


def render_detail_html(d: dict) -> str:
    e = html.escape
    symptoms = split_items(d["symptoms"])
    approaches = split_items(d["approach"])
    faqs = parse_faq(d["faq"])
    tags = [t.strip() for t in d["keywords"].split(",") if t.strip()][:6]
    canonical_url = f"{SITE_BASE_URL}/disease/{d['slug']}.html"

    img_block = (
        f'<div class="rounded-2xl overflow-hidden border border-slate-200 bg-[#faf5eb] mb-6">'
        f'<img src="../images/diseases/{e(d["image_file"])}" alt="{e(d["name"])}の鍼灸イメージ" class="w-full max-h-80 object-cover mx-auto" /></div>'
        if d["image_exists"] else ""
    )

    faq_ld_json = ""
    if faqs:
        ld_obj = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
                for f in faqs
            ],
        }
        faq_ld_json = f'<script type="application/ld+json">{json.dumps(ld_obj, ensure_ascii=False)}</script>'

    symptoms_html = ""
    if symptoms:
        lis = "".join(f'<li class="flex items-start gap-2.5 text-slate-700"><span class="text-[#b68d40] font-bold">✓</span><span>{e(s)}</span></li>' for s in symptoms)
        symptoms_html = f'<section class="mb-7"><h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] mb-3 flex items-center gap-2"><span class="w-1.5 h-5 bg-[#b68d40] rounded-full inline-block"></span>このようなお悩みはありませんか？</h2><ul class="space-y-2.5 bg-slate-50 p-5 rounded-xl border border-slate-200 text-sm sm:text-base">{lis}</ul></section>'

    approach_html = ""
    if approaches:
        cards = "".join(f'<div class="p-4 rounded-xl border border-slate-200 bg-white shadow-sm text-slate-700 text-sm sm:text-base leading-relaxed">{e(a)}</div>' for a in approaches)
        approach_html = f'<section class="mb-7"><h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] mb-3 flex items-center gap-2"><span class="w-1.5 h-5 bg-[#b68d40] rounded-full inline-block"></span>当院の施術アプローチ</h2><div class="space-y-3">{cards}</div></section>'

    faq_html = ""
    if faqs:
        cards = "".join(
            f'<div class="rounded-xl border border-slate-200 p-4 bg-slate-50/70"><p class="font-semibold text-[#16284c] text-sm sm:text-base">Q. {e(f["q"])}</p><p class="text-slate-600 text-sm mt-2 pl-4 border-l-2 border-[#b68d40] leading-relaxed">{e(f["a"])}</p></div>'
            for f in faqs
        )
        faq_html = f'<section class="mb-8"><h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] mb-3 flex items-center gap-2"><span class="w-1.5 h-5 bg-[#16284c] rounded-full inline-block"></span>よくある質問（FAQ）</h2><div class="space-y-3">{cards}</div></section>'

    tags_html = "".join(f'<span class="px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-600 text-xs">#{e(t)}</span>' for t in tags)

    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{e(d["name"])}の鍼灸｜銀座そうぜん鍼灸院グループ</title>
  <meta name="description" content="{e(d["meta_description"])}" />
  <link rel="canonical" href="{e(canonical_url)}" />
  {robots_meta_tag()}
  <script src="https://cdn.tailwindcss.com/3.4.17"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700&family=Noto+Serif+JP:wght@600;700&display=swap" rel="stylesheet" />
  <style>body {{ font-family: 'Noto Sans JP', sans-serif; background-color: #f4f7fb; color: #1e293b; }} .font-mincho {{ font-family: 'Noto Serif JP', serif; }}</style>
  {faq_ld_json}
</head>
<body class="min-h-screen antialiased">
  <div class="max-w-3xl mx-auto px-4 py-8 sm:py-10">
    <nav class="mb-5 text-xs text-slate-500 flex items-center gap-2">
      <a href="{e(PORTAL_URL)}" class="hover:text-[#16284c] underline">総合ポータル</a><span>/</span>
      <a href="./index.html" class="hover:text-[#16284c] underline">症状・疾患から探す</a><span>/</span>
      <span class="text-[#16284c] font-medium">{e(d["name"])}</span>
    </nav>
    <article class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      <header class="bg-[#16284c] text-white px-6 py-7">
        <span class="inline-block px-3 py-1 rounded-full text-xs font-semibold bg-[#b68d40] text-white mb-3">{e(d["category"])}</span>
        <h1 class="font-mincho text-2xl sm:text-3xl font-bold leading-snug">{e(d["name"])}の鍼灸</h1>
        <p class="text-slate-200 text-sm mt-3 leading-relaxed">{e(d["summary"])}</p>
      </header>
      <div class="p-6 sm:p-8">
        {img_block}
        {symptoms_html}
        <section class="mb-7">
          <h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] mb-3 flex items-center gap-2"><span class="w-1.5 h-5 bg-[#16284c] rounded-full inline-block"></span>原因・メカニズム</h2>
          <p class="text-slate-700 text-sm sm:text-base leading-relaxed">{e(d["mechanism"])}</p>
        </section>
        {approach_html}
        {faq_html}
        <div class="flex flex-wrap gap-2 mb-8">{tags_html}</div>
        <div class="rounded-2xl bg-[#faf5eb] border border-[#e5d5b5] p-6 text-center space-y-3">
          <p class="font-mincho text-base sm:text-lg font-bold text-[#16284c]">おひとりで悩まず、まずはご相談ください</p>
          <p class="text-xs sm:text-sm text-slate-600">銀座本院・川口・大宮・横浜・博多・田無の各院でご予約を承っております。</p>
          <div class="flex flex-wrap justify-center gap-3 pt-2">
            <a href="{e(PORTAL_URL)}" class="px-6 py-3 rounded-full bg-[#16284c] hover:bg-[#243b6b] text-white text-sm font-semibold transition-colors">ご予約へ進む（全国各院ポータル）→</a>
            <a href="./index.html" class="px-5 py-3 rounded-full bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 text-sm font-medium transition-colors">← 疾患一覧に戻る</a>
          </div>
        </div>
      </div>
    </article>
  </div>
</body>
</html>'''


def render_index_html(diseases: list[dict]) -> str:
    e = html.escape
    data_json = json.dumps(diseases, ensure_ascii=False)
    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>症状・疾患から探す｜そうぜん鍼灸院グループ</title>
  <meta name="description" content="銀座そうぜん鍼灸院グループが対応する疾患・症状を検索できる総合解説ページです。" />
  {robots_meta_tag()}
  <script src="https://cdn.tailwindcss.com/3.4.17"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700&family=Noto+Serif+JP:wght@600;700&display=swap" rel="stylesheet" />
  <style>body {{ font-family: 'Noto Sans JP', sans-serif; background-color: #f4f7fb; color: #1e293b; }} .font-mincho {{ font-family: 'Noto Serif JP', serif; }}</style>
</head>
<body class="min-h-screen antialiased">
  <div class="max-w-4xl mx-auto px-4 py-8 sm:py-10">
    <header class="text-center mb-7">
      <h1 class="font-mincho text-2xl sm:text-3xl font-bold text-[#16284c]">そうぜん鍼灸院グループ</h1>
      <p class="text-xs sm:text-sm text-slate-500 mt-1.5">症状・疾患から探す 解説ポータル</p>
      <div class="inline-flex items-center gap-2 bg-white p-1.5 rounded-full border border-slate-200 shadow-sm mt-5">
        <a href="{e(PORTAL_URL)}" class="px-4 sm:px-5 py-2 rounded-full text-xs sm:text-sm font-medium text-slate-600 hover:text-[#16284c]">🏥 鍼灸院から探す</a>
        <span class="px-4 sm:px-5 py-2 rounded-full text-xs sm:text-sm font-medium bg-[#16284c] text-white">📝 改善事例・症状から探す</span>
      </div>
    </header>
    <section class="bg-white rounded-2xl border border-slate-200 shadow-sm p-5 sm:p-6 space-y-4 mb-5">
      <div class="flex items-center justify-between gap-2">
        <div>
          <p class="text-[11px] font-semibold tracking-widest text-[#b68d40] uppercase">SYMPTOM &amp; DISEASE NAVIGATOR</p>
          <h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] mt-0.5">🌿 お悩み・疾患名から探す</h2>
        </div>
        <div class="text-xs text-slate-500">該当：<span id="count" class="font-bold text-[#16284c] text-sm">0</span> 件</div>
      </div>
      <input id="q" type="search" placeholder="疾患名・気になる症状を入力（例：頭鳴り、しびれ、自律神経）" class="w-full px-4 py-3 text-sm bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:outline-none focus:ring-2 focus:ring-[#16284c]" />
      <div id="cats" class="flex flex-wrap gap-2"></div>
    </section>
    <main id="list" class="grid grid-cols-1 gap-4"></main>
  </div>
  <script>
    const DISEASES = {data_json};
    let selectedCat = "すべて";
    let query = "";
    const esc = (s) => String(s || "").replace(/[&<>"']/g, (m) => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[m]));
    function render() {{
      const counts = {{ "すべて": DISEASES.length }};
      DISEASES.forEach((d) => {{ counts[d.category] = (counts[d.category] || 0) + 1; }});
      document.getElementById("cats").innerHTML = Object.keys(counts).map((c) => {{
        const active = c === selectedCat;
        const cls = active ? "bg-[#b68d40] text-white border-[#b68d40]" : "bg-slate-50 text-slate-600 border-slate-200 hover:border-[#b68d40]";
        return `<button type="button" data-cat="${{esc(c)}}" class="px-3.5 py-1.5 rounded-full text-xs font-medium border ${{cls}}">${{esc(c)}} (${{counts[c]}})</button>`;
      }}).join("");
      const q = query.trim().toLowerCase();
      const filtered = DISEASES.filter((d) => {{
        if (selectedCat !== "すべて" && d.category !== selectedCat) return false;
        if (!q) return true;
        return [d.name, d.category, d.keywords, d.summary, d.symptoms].join(" ").toLowerCase().includes(q);
      }});
      document.getElementById("count").textContent = filtered.length;
      document.getElementById("list").innerHTML = filtered.map((d) => {{
        const thumb = d.image_exists
          ? `<img src="../images/diseases/${{esc(d.image_file)}}" alt="${{esc(d.name)}}" class="w-full h-36 sm:h-full object-cover rounded-xl border border-slate-100" />`
          : `<div class="w-full h-36 sm:h-full min-h-[110px] rounded-xl bg-[#faf5eb] border border-[#e5d5b5] flex items-center justify-center text-[#b68d40] text-2xl">🩺</div>`;
        return `<a href="./${{esc(d.slug)}}.html" class="group bg-white rounded-2xl border border-slate-200 shadow-sm hover:border-[#b68d40] transition-all overflow-hidden flex"><div class="w-1.5 shrink-0 bg-[#16284c] group-hover:bg-[#b68d40]"></div><div class="flex-1 p-4 sm:p-5 grid grid-cols-1 sm:grid-cols-12 gap-4 items-center"><div class="sm:col-span-4">${{thumb}}</div><div class="sm:col-span-8 space-y-2"><span class="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-[#16284c] text-white">${{esc(d.category)}}</span><h3 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] group-hover:text-[#b68d40]">${{esc(d.name)}}</h3><p class="text-xs sm:text-sm text-slate-600 leading-relaxed">${{esc(d.summary)}}</p><div class="text-right text-xs font-semibold text-[#16284c]">詳しい解説を読む →</div></div></div></a>`;
      }}).join("") || '<p class="text-center text-sm text-slate-500 py-10 bg-white rounded-2xl border border-slate-200">該当する疾患が見つかりませんでした。</p>';
    }}
    document.getElementById("q").addEventListener("input", (e) => {{ query = e.target.value; render(); }});
    document.getElementById("cats").addEventListener("click", (e) => {{ const b = e.target.closest("button[data-cat]"); if (b) {{ selectedCat = b.dataset.cat; render(); }} }});
    render();
  </script>
</body>
</html>'''


def render_sitemap(diseases: list[dict]) -> str:
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    urls = [f"{SITE_BASE_URL}/", f"{SITE_BASE_URL}/disease/index.html"] + [f"{SITE_BASE_URL}/disease/{d['slug']}.html" for d in diseases]
    entries = "\n".join(f"  <url>\n    <loc>{html.escape(u)}</loc>\n    <lastmod>{today}</lastmod>\n  </url>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}\n</urlset>\n'


def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 else SHEET_CSV_URL
    if not source:
        print("ERROR: SHEET_CSV_URL が設定されていません。", file=sys.stderr)
        return 1
    diseases = parse_diseases(fetch_csv_text(source))
    if not diseases:
        print("WARNING: 生成対象が0件でした。", file=sys.stderr)
        return 0
    DISEASE_DIR.mkdir(parents=True, exist_ok=True)
    for d in diseases:
        (DISEASE_DIR / f"{d['slug']}.html").write_text(render_detail_html(d), encoding="utf-8")
    (DISEASE_DIR / "index.html").write_text(render_index_html(diseases), encoding="utf-8")
    (DISEASE_DIR / "diseases.json").write_text(json.dumps(diseases, ensure_ascii=False, indent=2), encoding="utf-8")
    (BASE_DIR / "sitemap.xml").write_text(render_sitemap(diseases), encoding="utf-8")
    print(f"OK: {len(diseases)} 件の疾患ページを生成しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
