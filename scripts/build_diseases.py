#!/usr/bin/env python3
import csv
import html
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DISEASE_DIR = BASE_DIR / "disease"
IMAGES_DIR = BASE_DIR / "images" / "diseases"
YOUTUBE_IMAGES_DIR = BASE_DIR / "images" / "youtube"
ARTICLES_DIR = BASE_DIR / "articles"
CATEGORY_EVIDENCE_PATH = Path(__file__).resolve().parent / "category_evidence.json"

SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "").strip()
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://sozen-harikyu.github.io/sozen-portal").rstrip("/")
PORTAL_URL = os.environ.get("PORTAL_URL", "https://sozen-harikyu.github.io/sozen-portal/").strip()
ALLOW_INDEXING = os.environ.get("ALLOW_INDEXING", "false").lower() == "true"
INCLUDE_DRAFTS = os.environ.get("INCLUDE_DRAFTS", "true").lower() == "true"
GENERATE_STATIC_REDIRECTS = os.environ.get("GENERATE_STATIC_REDIRECTS", "false").lower() == "true"

CATEGORY_EVIDENCE = (
    json.loads(CATEGORY_EVIDENCE_PATH.read_text(encoding="utf-8"))
    if CATEGORY_EVIDENCE_PATH.exists() else {}
)


def fetch_csv_text(source: str) -> str:
    if os.path.exists(source):
        return Path(source).read_text(encoding="utf-8-sig")
    req = urllib.request.Request(source, headers={"User-Agent": "sozen-portal-builder/2.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8-sig")


def sanitize_slug(slug: str, fallback_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", (slug or "").strip())
    return cleaned or re.sub(r"[^a-zA-Z0-9_-]", "", fallback_id.lower())


def extract_legacy_path(source_url: str) -> str:
    if not source_url or not source_url.startswith(("http://", "https://")):
        return ""
    parsed = urllib.parse.urlparse(source_url)
    if "s-treatment.com" not in (parsed.netloc or "").lower():
        return ""
    path = parsed.path.strip("/")
    if not path or "/" in path:
        return ""
    if re.match(r"^[a-zA-Z0-9_-]+$", path):
        return path
    return ""


def parse_diseases(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    diseases = []
    col_map = {}
    for col in (reader.fieldnames or []):
        c = col.strip()
        if c == "ID":
            col_map["id"] = col
        elif "疾患名" in c:
            col_map["name"] = col
        elif "カテゴリ" in c:
            col_map["category"] = col
        elif "検索キーワード" in c:
            col_map["keywords"] = col
        elif "slug" in c.lower():
            col_map["slug"] = col
        elif "meta_description" in c.lower():
            col_map["meta_description"] = col
        elif "一言要約" in c:
            col_map["summary"] = col
        elif "症状チェック" in c:
            col_map["symptoms"] = col
        elif "原因" in c:
            col_map["mechanism"] = col
        elif "施術アプローチ" in c:
            col_map["approach"] = col
        elif "FAQ" in c or "よくある質問" in c:
            col_map["faq"] = col
        elif "画像ファイル名" in c:
            col_map["image_file"] = col
        elif "参考URL" in c:
            col_map["source_url"] = col
        elif "公開ステータス" in c:
            col_map["status"] = col

    for row in reader:
        def get(k: str) -> str:
            return (row.get(col_map.get(k, ""), "") or "").strip()

        name = get("name")
        if not name:
            continue
        status = get("status")
        if not INCLUDE_DRAFTS and status != "公開":
            continue
        did = get("id")
        slug = sanitize_slug(get("slug"), did or name)
        category = get("category") or "その他のお悩み"
        img_file = get("image_file")
        if not (img_file and (IMAGES_DIR / img_file).exists()):
            cat_diag = (CATEGORY_EVIDENCE.get(category) or {}).get("diagram", "")
            if cat_diag and (IMAGES_DIR / cat_diag).exists():
                img_file = cat_diag
        img_exists = bool(img_file and (IMAGES_DIR / img_file).exists())
        has_youtube = (ARTICLES_DIR / f"{slug}_youtube.md").exists()
        has_rich = (ARTICLES_DIR / f"{slug}.html").exists()
        source_url = get("source_url")
        diseases.append({
            "id": did,
            "name": name,
            "category": category,
            "keywords": get("keywords"),
            "slug": slug,
            "meta_description": get("meta_description") or get("summary") or f"{name}に対する銀座そうぜん鍼灸院の鍼灸施術解説。",
            "summary": get("summary"),
            "symptoms": get("symptoms"),
            "mechanism": get("mechanism"),
            "approach": get("approach"),
            "faq": get("faq"),
            "image_file": img_file if img_exists else "",
            "image_exists": img_exists,
            "has_youtube": has_youtube,
            "has_rich": has_rich,
            "source_url": source_url,
            "legacy_path": extract_legacy_path(source_url),
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


def extract_youtube_id(url: str) -> str:
    url = (url or "").strip()
    m = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})", url)
    return m.group(1) if m else ""


def resolve_youtube_image_src(filename: str, slug: str) -> str:
    fn = filename.strip()
    if not fn:
        return ""
    if fn.startswith(("http://", "https://", "data:", "../", "./")):
        return fn
    candidates = [
        (YOUTUBE_IMAGES_DIR / slug / fn, f"../images/youtube/{slug}/{fn}"),
        (YOUTUBE_IMAGES_DIR / fn, f"../images/youtube/{fn}"),
        (IMAGES_DIR / fn, f"../images/diseases/{fn}"),
    ]
    for path_obj, rel_src in candidates:
        if path_obj.exists():
            return rel_src
    return ""


def render_youtube_markdown(md_text: str, slug: str) -> str:
    e = html.escape
    lines = md_text.splitlines()
    blocks: list[str] = []
    in_points = False
    point_items: list[str] = []
    para_lines: list[str] = []

    def flush_para() -> None:
        if para_lines:
            joined = "<br />".join(e(l) for l in para_lines)
            blocks.append(f'<p class="yt-para text-slate-700 text-[15px] sm:text-base leading-[1.9] mb-4">{joined}</p>')
            para_lines.clear()

    def flush_points() -> None:
        if point_items:
            lis = "".join(
                f'<li class="flex items-start gap-2.5 text-slate-800 text-[15px] sm:text-base leading-relaxed">'
                f'<span class="text-[#16284c] font-bold mt-0.5">•</span><span>{e(item)}</span></li>'
                for item in point_items
            )
            blocks.append(
                f'<div class="rounded-xl bg-[#eef7fc] border border-[#cde6f5] px-5 py-4 my-5">'
                f'<ul class="space-y-2">{lis}</ul></div>'
            )
            point_items.clear()

    for raw in lines:
        line = raw.strip()
        if line.startswith("<!--") and line.endswith("-->"):
            continue
        if line.startswith("# ") and not line.startswith("## "):
            continue
        if line == "[ポイント]":
            flush_para()
            in_points = True
            continue
        if line == "[/ポイント]":
            in_points = False
            flush_points()
            continue
        if in_points:
            if line:
                point_items.append(re.sub(r"^[・•\-*]\s*", "", line))
            continue
        if not line:
            flush_para()
            continue
        if line.startswith("## "):
            flush_para()
            title = line[3:].strip()
            blocks.append(
                f'<h2 class="yt-heading font-mincho text-xl sm:text-2xl font-bold text-[#16284c] text-center mt-8 mb-5 flex items-center justify-center gap-3">'
                f'<span class="text-[#0ea5e9] font-normal select-none" aria-hidden="true">＼</span>'
                f'<span>{e(title)}</span>'
                f'<span class="text-[#0ea5e9] font-normal select-none" aria-hidden="true">／</span></h2>'
            )
            continue
        if line.startswith("### "):
            flush_para()
            sub = line[4:].strip()
            blocks.append(
                f'<h3 class="font-mincho text-lg font-bold text-[#16284c] mt-6 mb-3 flex items-center gap-2">'
                f'<span class="w-1.5 h-5 bg-[#b68d40] rounded-full inline-block"></span>{e(sub)}</h3>'
            )
            continue
        img_match = re.match(r"^\[画像\s*[:：]\s*([^\]|]+?)(?:\s*\|\s*([^\]]+))?\]$", line)
        if img_match:
            flush_para()
            fname = img_match.group(1).strip()
            caption = (img_match.group(2) or "").strip()
            src = resolve_youtube_image_src(fname, slug)
            if src:
                cap_html = f'<figcaption class="text-xs text-slate-500 text-center mt-2">{e(caption)}</figcaption>' if caption else ""
                blocks.append(
                    f'<figure class="my-5 max-w-xl mx-auto">'
                    f'<img src="{e(src)}" alt="{e(caption or fname)}" loading="lazy" class="w-full rounded-xl border border-slate-200 shadow-sm" />'
                    f'{cap_html}</figure>'
                )
            continue
        yt_match = re.match(r"^\[YouTube\s*[:：]\s*([^\]]+)\]$", line, re.IGNORECASE)
        if yt_match:
            flush_para()
            vid = extract_youtube_id(yt_match.group(1))
            if vid:
                blocks.append(
                    f'<div class="my-6 max-w-2xl mx-auto rounded-xl overflow-hidden border border-slate-200 shadow-sm" style="aspect-ratio:16/9;">'
                    f'<iframe src="https://www.youtube.com/embed/{e(vid)}" title="YouTube解説動画" '
                    f'class="w-full h-full" frameborder="0" loading="lazy" '
                    f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></div>'
                )
            continue
        para_lines.append(line)

    flush_para()
    flush_points()
    if not blocks:
        return ""
    inner = "\n".join(blocks)
    return (
        '<section class="yt-section mb-10 pb-8 border-b border-slate-200">'
        '<div class="flex items-center justify-center mb-2">'
        '<span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-semibold bg-[#faf5eb] text-[#8c6239] border border-[#e5d5b5]">'
        '🎥 院長解説・YouTube切り抜き</span></div>'
        f'{inner}'
        '</section>'
    )


def load_custom_sections(slug: str) -> tuple[str, str]:
    rich_path = ARTICLES_DIR / f"{slug}.html"
    yt_path = ARTICLES_DIR / f"{slug}_youtube.md"
    rich_html = rich_path.read_text(encoding="utf-8") if rich_path.exists() else ""
    yt_html = render_youtube_markdown(yt_path.read_text(encoding="utf-8"), slug) if yt_path.exists() else ""
    return rich_html, yt_html


def render_category_rich_blocks(d: dict) -> str:
    ev = CATEGORY_EVIDENCE.get(d["category"])
    if not ev:
        return ""
    e = html.escape

    diag_file = ev.get("diagram", "")
    diag_html = ""
    if diag_file and (IMAGES_DIR / diag_file).exists():
        diag_html = (
            f'<figure class="diagram-frame">'
            f'<img src="../images/diseases/{e(diag_file)}" alt="{e(d["category"])}の代表的なツボ位置図解" loading="lazy" />'
            f'<figcaption>{e(ev.get("diagram_caption", ""))}</figcaption>'
            f'</figure>'
        )

    acu_rows = "".join(
        f'<tr><td class="col-primary">{e(row[0])}</td><td>{e(row[1])}</td><td>{e(row[2])}</td><td>{e(row[3])}</td></tr>'
        for row in ev.get("acupoints", [])
    )
    paper_rows = "".join(
        f'<tr><td class="col-primary">{e(p[0])}</td><td>{e(p[1])}</td><td>{e(p[2])}</td>'
        f'<td>{e(p[3])} <a href="{e(p[5])}" target="_blank" rel="noopener">{e(p[4])}</a></td></tr>'
        for p in ev.get("papers", [])
    )

    ref_items = []
    if d.get("source_url") and d["source_url"].startswith("http"):
        ref_items.append(
            f'<li>銀座そうぜん鍼灸院「<a href="{e(d["source_url"])}" target="_blank" rel="noopener">{e(d["name"])}の公式解説ページ</a>」</li>'
        )
    for p in ev.get("papers", []):
        ref_items.append(
            f'<li>{e(p[0])}（{e(p[1])}）— <a href="{e(p[5])}" target="_blank" rel="noopener">{e(p[4])}</a></li>'
        )
    refs_html = "".join(ref_items)

    return f'''
<h2>{e(d["category"])}で代表的なツボとセルフケアの目安</h2>
<p>実際の施術ではお一人おひとりの体質・症状に合わせて選穴しますが、{e(ev["domain_label"])}の臨床でよく用いられる代表的な経穴（ツボ）は以下の通りです。</p>
{diag_html}
<div class="scroll-hint">← 表は横にスワイプして見られます →</div>
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>ツボ名（経穴）</th>
        <th>位置の目安</th>
        <th>主な狙い・役割</th>
        <th>セルフケアの注意点</th>
      </tr>
    </thead>
    <tbody>{acu_rows}</tbody>
  </table>
</div>

<h2>査読論文が示す「{e(ev["domain_label"])}×鍼灸」の科学的エビデンス</h2>
<p>個別疾患ごとの大規模試験が限られる領域も含め、国際的な医学データベース（PubMed・Cochrane等）で報告されている<strong>{e(ev["domain_label"])}領域の代表的な査読済み鍼灸研究</strong>をまとめました（いずれも標準治療との併用や研究上の限界を含めて正確に掲載しています）。</p>
<div class="scroll-hint">← 表は横にスワイプして見られます →</div>
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>年・研究デザイン</th>
        <th>対象・規模</th>
        <th>主な結果と臨床的意義</th>
        <th>限界・出典</th>
      </tr>
    </thead>
    <tbody>{paper_rows}</tbody>
  </table>
</div>

<h2>医療機関の受診を最優先すべきサイン（レッドフラッグ）</h2>
<div class="warn-box">
  <p class="font-bold text-[#8c6239] text-sm sm:text-base mb-1">⚠ 専門医の受診・検査を最優先してください</p>
  <p class="text-sm text-slate-700 mb-0">{e(ev["red_flags"])}</p>
</div>

<h2>参考文献・出典</h2>
<ol class="ref-list">{refs_html}</ol>
'''


def pick_related_diseases(d: dict, by_category: dict[str, list[dict]], limit: int = 6) -> list[dict]:
    """同カテゴリからキーワード重複度・詳細記事有無を優先して関連疾患を最大6件選出する。"""
    peers = [p for p in by_category.get(d["category"], []) if p["slug"] != d["slug"]]
    if not peers:
        return []
    my_kws = {k.strip() for k in d["keywords"].split(",") if k.strip()}

    def score(p: dict) -> tuple[int, int, int, str]:
        p_kws = {k.strip() for k in p["keywords"].split(",") if k.strip()}
        overlap = len(my_kws & p_kws)
        return (
            1 if p["has_rich"] else 0,
            1 if p["has_youtube"] else 0,
            overlap,
            p["id"],
        )

    ranked = sorted(peers, key=score, reverse=True)
    return ranked[:limit]


def render_related_html(related: list[dict], category: str) -> str:
    if not related:
        return ""
    e = html.escape
    cards = "".join(
        f'<a href="./{e(r["slug"])}.html" class="group block rounded-xl border border-slate-200 bg-slate-50/70 hover:bg-white hover:border-[#b68d40] p-3.5 transition-all">'
        f'<div class="flex items-center justify-between gap-2">'
        f'<span class="font-mincho font-bold text-sm text-[#16284c] group-hover:text-[#b68d40]">{e(r["name"])}</span>'
        f'<span class="text-xs text-slate-400 group-hover:text-[#b68d40] shrink-0">→</span></div>'
        f'<p class="text-xs text-slate-600 mt-1 line-clamp-2 leading-relaxed">{e(r["summary"])}</p>'
        f'</a>'
        for r in related
    )
    return (
        f'<section class="mb-8 mt-8 pt-6 border-t border-slate-200">'
        f'<h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] mb-3 flex items-center gap-2">'
        f'<span class="w-1.5 h-5 bg-[#b68d40] rounded-full inline-block"></span>'
        f'あわせて読まれる関連疾患（{e(category)}）</h2>'
        f'<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">{cards}</div>'
        f'</section>'
    )


RICH_ARTICLE_CSS = """
  .rich-article { color: #1e293b; font-size: 15.5px; line-height: 1.9; }
  .rich-article h2 { font-family: 'Noto Serif JP', serif; font-size: clamp(1.25rem, 2.6vw, 1.65rem); font-weight: 700; color: #16284c; margin: 2.4rem 0 1rem; padding-bottom: 0.45rem; border-bottom: 2px solid #e2e8f0; line-height: 1.4; }
  .rich-article h3 { font-family: 'Noto Serif JP', serif; font-size: clamp(1.05rem, 2vw, 1.2rem); font-weight: 700; color: #16284c; margin: 1.5rem 0 0.6rem; line-height: 1.45; }
  .rich-article p { margin: 0 0 1rem; color: #334155; }
  .rich-article a { color: #16284c; text-decoration: underline; text-underline-offset: 3px; }
  .rich-article a:hover { color: #b68d40; }
  .rich-article .verdict { border-top: 2px solid #16284c; border-bottom: 1px solid #cbd5e1; padding: 16px 0; margin: 0 0 24px; font-family: 'Noto Serif JP', serif; font-size: clamp(1rem, 2vw, 1.16rem); line-height: 1.75; color: #16284c; }
  .rich-article .verdict strong { color: #8c6239; }
  .rich-article .diagram-frame { margin: 22px 0; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 12px; }
  .rich-article .diagram-frame img { display: block; width: 100%; height: auto; margin: 0 auto; border-radius: 8px; }
  .rich-article figcaption { font-size: 12.5px; color: #64748b; line-height: 1.65; margin-top: 10px; }
  .rich-article .scroll-hint { display: none; }
  .rich-article .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 14px 0 24px; border: 1px solid #e2e8f0; border-radius: 12px; background: #ffffff; }
  .rich-article table { width: 100%; border-collapse: collapse; font-size: 14px; line-height: 1.65; }
  .rich-article thead th { background: #16284c; color: #ffffff; text-align: left; font-weight: 600; padding: 11px 12px; white-space: nowrap; font-size: 13px; }
  .rich-article tbody td { padding: 12px; border-bottom: 1px solid #e2e8f0; vertical-align: top; color: #334155; }
  .rich-article tbody tr:last-child td { border-bottom: none; }
  .rich-article .col-primary { font-weight: 700; color: #16284c; white-space: nowrap; background: #f8fafc; }
  .rich-article .chart-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 18px 0 24px; padding: 16px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 14px; }
  .rich-article .chart-inner { min-width: 560px; }
  .rich-article .band-box { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 14px; padding: 20px; margin: 22px 0; }
  .rich-article .warn-box { background: #fffbeb; border-left: 4px solid #b68d40; border-radius: 0 12px 12px 0; padding: 16px 18px; margin: 18px 0; }
  .rich-article .pull-quote { border-left: 4px solid #16284c; padding: 12px 18px; margin: 24px 0; background: #faf5eb; border-radius: 0 12px 12px 0; font-family: 'Noto Serif JP', serif; color: #16284c; font-size: 15.5px; line-height: 1.8; }
  .rich-article .ref-list { padding-left: 1.25rem; font-size: 13.5px; color: #475569; line-height: 1.75; }
  .rich-article .ref-list li { margin-bottom: 8px; word-break: break-word; }
  @media (max-width: 640px) {
    .rich-article { font-size: 16px; line-height: 1.88; }
    .rich-article .scroll-hint { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; color: #8c6239; background: #faf5eb; border: 1px solid #e5d5b5; padding: 4px 10px; border-radius: 999px; margin-bottom: 8px; }
    .rich-article table { min-width: 580px; font-size: 13.5px; }
    .rich-article thead th:first-child,
    .rich-article tbody td:first-child { position: sticky; left: 0; z-index: 2; box-shadow: 2px 0 6px rgba(15, 23, 42, 0.08); }
    .rich-article thead th:first-child { background: #16284c; }
    .rich-article tbody td:first-child { background: #f8fafc; }
  }
"""


def render_detail_html(d: dict, by_category: dict[str, list[dict]]) -> str:
    e = html.escape
    symptoms = split_items(d["symptoms"])
    approaches = split_items(d["approach"])
    faqs = parse_faq(d["faq"])
    tags = [t.strip() for t in d["keywords"].split(",") if t.strip()][:6]
    canonical_url = f"{SITE_BASE_URL}/disease/{d['slug']}.html"
    og_image = f"{SITE_BASE_URL}/images/diseases/{d['image_file']}" if d["image_exists"] else ""

    rich_html, yt_html = load_custom_sections(d["slug"])

    img_block = (
        f'<div class="rounded-2xl overflow-hidden border border-slate-200 bg-[#faf5eb] mb-6">'
        f'<img src="../images/diseases/{e(d["image_file"])}" alt="{e(d["name"])}の鍼灸イメージ" class="w-full max-h-80 object-cover mx-auto" /></div>'
        if d["image_exists"] else ""
    )

    ld_blocks = []
    medical_ld = {
        "@context": "https://schema.org",
        "@type": "MedicalWebPage",
        "name": f"{d['name']}の鍼灸｜銀座そうぜん鍼灸院グループ",
        "description": d["meta_description"],
        "url": canonical_url,
        "inLanguage": "ja",
        "about": {
            "@type": "MedicalCondition",
            "name": d["name"],
        },
        "publisher": {
            "@type": "MedicalClinic",
            "name": "銀座そうぜん鍼灸院グループ",
            "url": PORTAL_URL,
        },
    }
    ld_blocks.append(f'<script type="application/ld+json">{json.dumps(medical_ld, ensure_ascii=False)}</script>')

    breadcrumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "総合ポータル", "item": PORTAL_URL},
            {"@type": "ListItem", "position": 2, "name": "症状・疾患から探す", "item": f"{SITE_BASE_URL}/disease/index.html"},
            {"@type": "ListItem", "position": 3, "name": d["name"], "item": canonical_url},
        ],
    }
    ld_blocks.append(f'<script type="application/ld+json">{json.dumps(breadcrumb_ld, ensure_ascii=False)}</script>')

    if faqs:
        faq_ld = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
                for f in faqs
            ],
        }
        ld_blocks.append(f'<script type="application/ld+json">{json.dumps(faq_ld, ensure_ascii=False)}</script>')

    structured_ld_html = "\n  ".join(ld_blocks)
    og_image_meta = f'<meta property="og:image" content="{e(og_image)}" />' if og_image else ""

    symptoms_html = ""
    if symptoms:
        lis = "".join(f'<li class="flex items-start gap-2.5 text-slate-700"><span class="text-[#b68d40] font-bold">✓</span><span>{e(s)}</span></li>' for s in symptoms)
        symptoms_html = f'<section class="mb-7"><h2 class="!mt-6">このようなお悩みはありませんか？</h2><ul class="space-y-2.5 bg-slate-50 p-5 rounded-xl border border-slate-200 text-sm sm:text-base">{lis}</ul></section>'

    approach_html = ""
    if approaches:
        cards = "".join(f'<div class="p-4 rounded-xl border border-slate-200 bg-white shadow-sm text-slate-700 text-sm sm:text-base leading-relaxed">{e(a)}</div>' for a in approaches)
        approach_html = f'<section class="mb-7"><h2>当院の施術アプローチ</h2><div class="space-y-3">{cards}</div></section>'

    faq_html = ""
    if faqs:
        cards = "".join(
            f'<div class="rounded-xl border border-slate-200 p-4 bg-slate-50/70"><p class="font-semibold text-[#16284c] text-sm sm:text-base">Q. {e(f["q"])}</p><p class="text-slate-600 text-sm mt-2 pl-4 border-l-2 border-[#b68d40] leading-relaxed">{e(f["a"])}</p></div>'
            for f in faqs
        )
        faq_html = f'<section class="mb-8 mt-8"><h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] mb-3 flex items-center gap-2"><span class="w-1.5 h-5 bg-[#16284c] rounded-full inline-block"></span>よくある質問（FAQ）</h2><div class="space-y-3">{cards}</div></section>'

    tags_html = "".join(f'<span class="px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-600 text-xs">#{e(t)}</span>' for t in tags)
    related_html = render_related_html(pick_related_diseases(d, by_category, limit=6), d["category"])

    if rich_html:
        if "<!-- YOUTUBE_SECTION -->" in rich_html:
            body_content = rich_html.replace("<!-- YOUTUBE_SECTION -->", yt_html)
        else:
            body_content = yt_html + "\n" + rich_html
        main_body_html = f'<div class="rich-article">{body_content}</div>{faq_html}'
    else:
        src_link = (
            f'（<a href="{e(d["source_url"])}" target="_blank" rel="noopener">銀座そうぜん鍼灸院 公式解説</a>）'
            if d.get("source_url") and d["source_url"].startswith("http") else ""
        )
        verdict_html = f'<div class="verdict">{e(d["summary"])}{src_link}</div>' if d["summary"] else ""
        cat_rich_html = render_category_rich_blocks(d)
        main_body_html = (
            f'<div class="rich-article">'
            f'{yt_html}'
            f'{verdict_html}'
            f'{img_block}'
            f'{symptoms_html}'
            f'<section class="mb-7">'
            f'<h2>原因・メカニズム</h2>'
            f'<p>{e(d["mechanism"])}</p>'
            f'</section>'
            f'{approach_html}'
            f'{cat_rich_html}'
            f'</div>'
            f'{faq_html}'
        )

    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{e(d["name"])}の鍼灸｜銀座そうぜん鍼灸院グループ</title>
  <meta name="description" content="{e(d["meta_description"])}" />
  <link rel="canonical" href="{e(canonical_url)}" />
  <meta property="og:type" content="article" />
  <meta property="og:site_name" content="銀座そうぜん鍼灸院グループ" />
  <meta property="og:title" content="{e(d["name"])}の鍼灸｜銀座そうぜん鍼灸院グループ" />
  <meta property="og:description" content="{e(d["meta_description"])}" />
  <meta property="og:url" content="{e(canonical_url)}" />
  {og_image_meta}
  <meta name="twitter:card" content="summary_large_image" />
  {robots_meta_tag()}
  <script src="https://cdn.tailwindcss.com/3.4.17"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700&family=Noto+Serif+JP:wght@600;700&display=swap" rel="stylesheet" />
  <style>body {{ font-family: 'Noto Sans JP', sans-serif; background-color: #f4f7fb; color: #1e293b; }} .font-mincho {{ font-family: 'Noto Serif JP', serif; }}{RICH_ARTICLE_CSS}</style>
  {structured_ld_html}
</head>
<body class="min-h-screen antialiased pb-24 sm:pb-10">
  <div class="max-w-3xl mx-auto px-3 sm:px-4 py-6 sm:py-10">
    <nav class="mb-5 text-xs text-slate-500 flex flex-wrap items-center gap-2">
      <a href="{e(PORTAL_URL)}" class="hover:text-[#16284c] underline">総合ポータル</a><span>/</span>
      <a href="./index.html" class="hover:text-[#16284c] underline">症状・疾患から探す</a><span>/</span>
      <span class="text-[#16284c] font-medium">{e(d["name"])}</span>
    </nav>
    <article class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      <header class="bg-[#16284c] text-white px-5 sm:px-7 py-7">
        <span class="inline-block px-3 py-1 rounded-full text-xs font-semibold bg-[#b68d40] text-white mb-3">{e(d["category"])}</span>
        <h1 class="font-mincho text-2xl sm:text-3xl font-bold leading-snug">{e(d["name"])}の鍼灸</h1>
        <p class="text-slate-200 text-sm mt-3 leading-relaxed">{e(d["summary"])}</p>
      </header>
      <div class="p-5 sm:p-8">
        {main_body_html}
        <div class="flex flex-wrap gap-2 mb-6">{tags_html}</div>
        {related_html}
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
  <div class="sm:hidden fixed bottom-0 inset-x-0 z-40 bg-white/95 backdrop-blur border-t border-slate-200 shadow-[0_-4px_16px_rgba(15,23,42,0.08)] px-3 py-2.5">
    <div class="max-w-lg mx-auto grid grid-cols-12 gap-2 items-center">
      <a href="{e(PORTAL_URL)}" class="col-span-6 py-2.5 px-3 rounded-full bg-[#16284c] text-white text-xs font-bold text-center shadow-sm active:scale-95 transition-transform">📅 空き確認・ご予約</a>
      <a href="../symptom-check.html" class="col-span-3 py-2.5 px-2 rounded-full bg-[#faf5eb] border border-[#e5d5b5] text-[#8c6239] text-[11px] font-bold text-center">🧭 症状ナビ</a>
      <a href="./index.html" class="col-span-3 py-2.5 px-2 rounded-full bg-slate-100 text-slate-700 text-[11px] font-semibold text-center">🔍 一覧</a>
    </div>
  </div>
</body>
</html>'''


def render_index_html(diseases: list[dict]) -> str:
    e = html.escape
    slim = [
        {
            "id": d["id"],
            "name": d["name"],
            "category": d["category"],
            "keywords": d["keywords"],
            "slug": d["slug"],
            "summary": d["summary"],
            "symptoms": d["symptoms"],
            "image_file": d["image_file"],
            "image_exists": d["image_exists"],
            "has_youtube": d["has_youtube"],
            "has_rich": d["has_rich"],
        }
        for d in diseases
    ]
    data_json = json.dumps(slim, ensure_ascii=False)
    popular_chips = ["頭鳴り", "脳過敏", "耳鳴り", "めまい", "片頭痛", "自律神経", "不眠", "坐骨神経痛", "五十肩", "帯状疱疹", "更年期", "咳喘息"]
    chips_html = "".join(
        f'<button type="button" data-quick="{e(kw)}" class="px-3 py-1 rounded-full text-xs font-medium bg-[#faf5eb] text-[#8c6239] border border-[#e5d5b5] hover:bg-[#b68d40] hover:text-white transition-colors">{e(kw)}</button>'
        for kw in popular_chips
    )
    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>症状・疾患から探す｜そうぜん鍼灸院グループ</title>
  <meta name="description" content="銀座そうぜん鍼灸院グループが対応する全1,085疾患・症状をひらがな・キーワード・カテゴリから検索できる総合解説ページです。" />
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
        <a href="{e(PORTAL_URL)}" class="px-3.5 sm:px-5 py-2 rounded-full text-xs sm:text-sm font-medium text-slate-600 hover:text-[#16284c]">🏥 鍼灸院から探す</a>
        <span class="px-3.5 sm:px-5 py-2 rounded-full text-xs sm:text-sm font-medium bg-[#16284c] text-white">📝 症状から探す</span>
        <a href="../symptom-check.html" class="px-3.5 sm:px-4 py-2 rounded-full text-xs sm:text-sm font-semibold text-[#8c6239] hover:bg-[#faf5eb]">🧭 30秒ナビ</a>
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
      <div class="relative">
        <input id="q" type="search" placeholder="疾患名・気になる症状を入力（例：ずなり、めまい、しびれ、自律神経）" class="w-full px-4 py-3 text-sm bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:outline-none focus:ring-2 focus:ring-[#16284c]" />
      </div>
      <div class="space-y-1.5">
        <p class="text-[11px] font-semibold text-slate-500">よく相談される症状からワンタップ検索</p>
        <div id="quick-chips" class="flex flex-wrap gap-1.5">{chips_html}</div>
      </div>
      <div id="cats" class="flex flex-wrap gap-2 pt-1 border-t border-slate-100"></div>
    </section>
    <main id="list" class="grid grid-cols-1 gap-4"></main>
    <div id="load-more-wrap" class="mt-6 text-center hidden">
      <button id="load-more-btn" type="button" class="px-6 py-3 rounded-full bg-white border border-slate-300 hover:border-[#16284c] text-[#16284c] text-sm font-semibold shadow-sm transition-colors"></button>
    </div>
  </div>
  <script>
    const DISEASES = {data_json};
    let selectedCat = "すべて";
    let query = "";
    let visibleLimit = 30;
    const esc = (s) => String(s || "").replace(/[&<>"']/g, (m) => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[m]));
    const norm = (s) => String(s || "")
      .normalize("NFKC")
      .toLowerCase()
      .replace(/[ァ-ヶ]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0x60))
      .replace(/\s+/g, " ")
      .trim();

    const INDEXED = DISEASES.map((d) => ({{
      ...d,
      _haystack: norm([d.name, d.category, d.keywords, d.summary, d.symptoms].join(" "))
    }}));

    function cardHtml(d) {{
      const thumb = d.image_exists
        ? `<img src="../images/diseases/${{esc(d.image_file)}}" alt="${{esc(d.name)}}" loading="lazy" class="w-full h-36 sm:h-full object-cover rounded-xl border border-slate-100" />`
        : `<div class="w-full h-36 sm:h-full min-h-[110px] rounded-xl bg-[#faf5eb] border border-[#e5d5b5] flex items-center justify-center text-[#b68d40] text-2xl">🩺</div>`;
      const badges = [
        `<span class="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-[#16284c] text-white">${{esc(d.category)}}</span>`,
        d.has_youtube ? `<span class="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-[#eef7fc] text-[#0369a1] border border-[#bae6fd]">🎥 YouTube解説あり</span>` : "",
        d.has_rich ? `<span class="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-[#faf5eb] text-[#8c6239] border border-[#e5d5b5]">★ 詳細解説</span>` : ""
      ].filter(Boolean).join(" ");
      return `<a href="./${{esc(d.slug)}}.html" class="group bg-white rounded-2xl border border-slate-200 shadow-sm hover:border-[#b68d40] transition-all overflow-hidden flex"><div class="w-1.5 shrink-0 bg-[#16284c] group-hover:bg-[#b68d40]"></div><div class="flex-1 p-4 sm:p-5 grid grid-cols-1 sm:grid-cols-12 gap-4 items-center"><div class="sm:col-span-4">${{thumb}}</div><div class="sm:col-span-8 space-y-2"><div class="flex flex-wrap items-center gap-1.5">${{badges}}</div><h3 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] group-hover:text-[#b68d40]">${{esc(d.name)}}</h3><p class="text-xs sm:text-sm text-slate-600 leading-relaxed">${{esc(d.summary)}}</p><div class="text-right text-xs font-semibold text-[#16284c]">詳しい解説を読む →</div></div></div></a>`;
    }}

    function render() {{
      const counts = {{ "すべて": INDEXED.length }};
      INDEXED.forEach((d) => {{ counts[d.category] = (counts[d.category] || 0) + 1; }});
      document.getElementById("cats").innerHTML = Object.keys(counts).map((c) => {{
        const active = c === selectedCat;
        const cls = active ? "bg-[#b68d40] text-white border-[#b68d40]" : "bg-slate-50 text-slate-600 border-slate-200 hover:border-[#b68d40]";
        return `<button type="button" data-cat="${{esc(c)}}" class="px-3.5 py-1.5 rounded-full text-xs font-medium border ${{cls}}">${{esc(c)}} (${{counts[c]}})</button>`;
      }}).join("");

      const tokens = norm(query).split(" ").filter(Boolean);
      const filtered = INDEXED.filter((d) => {{
        if (selectedCat !== "すべて" && d.category !== selectedCat) return false;
        if (!tokens.length) return true;
        return tokens.every((tk) => d._haystack.includes(tk));
      }});

      document.getElementById("count").textContent = filtered.length;
      const slice = filtered.slice(0, visibleLimit);
      document.getElementById("list").innerHTML = slice.map(cardHtml).join("") || '<p class="text-center text-sm text-slate-500 py-10 bg-white rounded-2xl border border-slate-200">該当する疾患が見つかりませんでした。別のひらがな・キーワードでお試しください。</p>';

      const remaining = filtered.length - slice.length;
      const wrap = document.getElementById("load-more-wrap");
      const btn = document.getElementById("load-more-btn");
      if (remaining > 0) {{
        wrap.classList.remove("hidden");
        btn.textContent = `さらに表示する（残り ${{remaining}} 件）`;
      }} else {{
        wrap.classList.add("hidden");
      }}
    }}

    document.getElementById("q").addEventListener("input", (e) => {{ query = e.target.value; visibleLimit = 30; render(); }});
    document.getElementById("cats").addEventListener("click", (e) => {{
      const b = e.target.closest("button[data-cat]");
      if (b) {{ selectedCat = b.dataset.cat; visibleLimit = 30; render(); }}
    }});
    document.getElementById("quick-chips").addEventListener("click", (e) => {{
      const b = e.target.closest("button[data-quick]");
      if (b) {{
        query = b.dataset.quick;
        selectedCat = "すべて";
        visibleLimit = 30;
        document.getElementById("q").value = query;
        render();
      }}
    }});
    document.getElementById("load-more-btn").addEventListener("click", () => {{ visibleLimit += 50; render(); }});
    render();
  </script>
</body>
</html>'''


def render_sitemap(diseases: list[dict]) -> str:
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
    urls = [f"{SITE_BASE_URL}/", f"{SITE_BASE_URL}/disease/index.html"] + [f"{SITE_BASE_URL}/disease/{d['slug']}.html" for d in diseases]
    entries = "\n".join(f"  <url>\n    <loc>{html.escape(u)}</loc>\n    <lastmod>{today}</lastmod>\n  </url>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}\n</urlset>\n'


def write_static_redirect_page(legacy_path: str, slug: str) -> None:
    target_url = f"{SITE_BASE_URL}/disease/{slug}.html"
    e = html.escape
    dest_dir = BASE_DIR / legacy_path
    dest_dir.mkdir(parents=True, exist_ok=True)
    content = (
        f'<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8" />'
        f'<meta http-equiv="refresh" content="0; url={e(target_url)}" />'
        f'<link rel="canonical" href="{e(target_url)}" />'
        f'<title>ページを移動しました</title></head>'
        f'<body><p><a href="{e(target_url)}">新しいページへ移動する</a></p></body></html>'
    )
    (dest_dir / "index.html").write_text(content, encoding="utf-8")


def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 else SHEET_CSV_URL
    if not source:
        print("ERROR: SHEET_CSV_URL が設定されていません。", file=sys.stderr)
        return 1
    diseases = parse_diseases(fetch_csv_text(source))
    if not diseases:
        print("WARNING: 生成対象が0件でした。", file=sys.stderr)
        return 0

    by_category: dict[str, list[dict]] = {}
    legacy_map: dict[str, str] = {}
    for d in diseases:
        by_category.setdefault(d["category"], []).append(d)
        if d["legacy_path"]:
            legacy_map[d["legacy_path"]] = d["slug"]

    DISEASE_DIR.mkdir(parents=True, exist_ok=True)
    for d in diseases:
        (DISEASE_DIR / f"{d['slug']}.html").write_text(render_detail_html(d, by_category), encoding="utf-8")
        if GENERATE_STATIC_REDIRECTS and d["legacy_path"]:
            write_static_redirect_page(d["legacy_path"], d["slug"])

    (DISEASE_DIR / "index.html").write_text(render_index_html(diseases), encoding="utf-8")
    (DISEASE_DIR / "diseases.json").write_text(json.dumps(diseases, ensure_ascii=False, indent=2), encoding="utf-8")
    (DISEASE_DIR / "legacy_redirects.json").write_text(json.dumps(legacy_map, ensure_ascii=False, indent=2), encoding="utf-8")
    (BASE_DIR / "sitemap.xml").write_text(render_sitemap(diseases), encoding="utf-8")
    print(f"OK: {len(diseases)} 件の疾患ページ・関連疾患・構造化データ・旧URLマップ({len(legacy_map)}件)を生成しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
