#!/usr/bin/env python3
import csv
import hashlib
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
YOUTUBE_VIDEOS_PATH = Path(__file__).resolve().parent / "youtube_channel_videos.json"

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
YOUTUBE_CATALOG = (
    json.loads(YOUTUBE_VIDEOS_PATH.read_text(encoding="utf-8"))
    if YOUTUBE_VIDEOS_PATH.exists() else {}
)
YT_CHANNEL_URL = YOUTUBE_CATALOG.get("channel_url", "https://www.youtube.com/channel/UCY62Fl_Z19qeV4B2asWDlVg")
YT_BY_SLUG = YOUTUBE_CATALOG.get("by_slug", {})


def fetch_csv_text(source: str) -> str:
    if os.path.exists(source):
        return Path(source).read_text(encoding="utf-8-sig")
    req = urllib.request.Request(source, headers={"User-Agent": "sozen-portal-builder/3.0"})
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
        has_youtube = (ARTICLES_DIR / f"{slug}_youtube.md").exists() or bool(YT_BY_SLUG.get(slug))
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


def _render_yt_chunk(lines: list[str], slug: str) -> str:
    e = html.escape
    blocks: list[str] = []
    in_points = False
    point_items: list[str] = []
    para_lines: list[str] = []

    def flush_para() -> None:
        if para_lines:
            joined = "<br />".join(e(l) for l in para_lines)
            blocks.append(f'<p class="text-slate-700 text-sm sm:text-[15px] leading-[1.85] mb-3.5">{joined}</p>')
            para_lines.clear()

    def flush_points() -> None:
        if point_items:
            lis = "".join(
                f'<li class="flex items-start gap-2 text-slate-800 text-sm leading-relaxed">'
                f'<span class="text-[#16284c] font-bold">•</span><span>{e(item)}</span></li>'
                for item in point_items
            )
            blocks.append(
                f'<div class="rounded-2xl bg-[#eef7fc] border border-[#cde6f5] px-5 py-4 my-4">'
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
                f'<h3 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] text-center mt-2 mb-4 flex items-center justify-center gap-2.5">'
                f'<span class="text-[#0ea5e9] font-normal select-none" aria-hidden="true">＼</span>'
                f'<span>{e(title)}</span>'
                f'<span class="text-[#0ea5e9] font-normal select-none" aria-hidden="true">／</span></h3>'
            )
            continue
        if line.startswith("### "):
            flush_para()
            sub = line[4:].strip()
            blocks.append(
                f'<h4 class="font-mincho text-base font-bold text-[#16284c] mt-4 mb-2 flex items-center gap-2">'
                f'<span class="w-1.5 h-4 bg-[#b68d40] rounded-full inline-block"></span>{e(sub)}</h4>'
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
                    f'<figure class="my-4 max-w-lg mx-auto">'
                    f'<img src="{e(src)}" alt="{e(caption or fname)}" loading="lazy" class="w-full rounded-2xl border border-slate-200 shadow-sm" />'
                    f'{cap_html}</figure>'
                )
            continue
        yt_match = re.match(r"^\[YouTube\s*[:：]\s*([^\]]+)\]$", line, re.IGNORECASE)
        if yt_match:
            flush_para()
            vid = extract_youtube_id(yt_match.group(1))
            if vid:
                blocks.append(
                    f'<div class="my-4 max-w-xl mx-auto rounded-2xl overflow-hidden border border-slate-200 shadow-sm" style="aspect-ratio:16/9;">'
                    f'<iframe src="https://www.youtube.com/embed/{e(vid)}" title="YouTube解説動画" '
                    f'class="w-full h-full" frameborder="0" loading="lazy" '
                    f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></div>'
                )
            continue
        para_lines.append(line)

    flush_para()
    flush_points()
    return "\n".join(blocks)


def render_youtube_section_card(d: dict) -> str:
    """公式YouTube動画紹介＋厳選切り抜き（1本目表示・2本目以降折りたたみ）を1枚の美しいカードにまとめる。"""
    e = html.escape
    slug = d["slug"]
    videos = YT_BY_SLUG.get(slug, [])

    # 公式チャンネル動画カード（「このような説明をしている動画」紹介付き）
    videos_html = ""
    if videos:
        v_cards = []
        for v in videos:
            vid = v["id"]
            vurl = f"https://www.youtube.com/watch?v={vid}"
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
            v_cards.append(
                f'<a href="{e(vurl)}" target="_blank" rel="noopener" '
                f'class="group grid grid-cols-1 sm:grid-cols-12 gap-3.5 items-center rounded-2xl border border-slate-200 bg-[#faf8f4] hover:bg-white hover:border-[#b68d40] p-3.5 transition-all">'
                f'<div class="sm:col-span-5 relative rounded-xl overflow-hidden bg-slate-900 aspect-video">'
                f'<img src="{e(thumb)}" alt="{e(v["title"])}" loading="lazy" class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" />'
                f'<span class="absolute bottom-2 right-2 px-2 py-0.5 rounded-md bg-red-600 text-white text-[10px] font-bold">▶ YouTube</span>'
                f'</div>'
                f'<div class="sm:col-span-7 space-y-1.5">'
                f'<p class="font-mincho font-bold text-sm sm:text-[15px] text-[#16284c] group-hover:text-[#b68d40] leading-snug">{e(v["title"])}</p>'
                f'<p class="text-xs text-slate-600 leading-relaxed">{e(v["summary"])}</p>'
                f'<span class="inline-block text-[11px] font-semibold text-[#0369a1]">動画を見る（YouTubeへ） →</span>'
                f'</div></a>'
            )
        if len(v_cards) == 1:
            videos_html = f'<div class="space-y-3">{v_cards[0]}</div>'
        else:
            extra_v = "".join(v_cards[1:])
            videos_html = (
                f'<div class="space-y-3">{v_cards[0]}'
                f'<details class="rounded-2xl border border-slate-200 bg-slate-50/70 overflow-hidden">'
                f'<summary class="px-4 py-3 flex items-center justify-between text-xs sm:text-sm font-semibold text-[#16284c] hover:bg-slate-100/70">'
                f'<span>▶ 関連する院長YouTube動画をもっと見る（あと{len(v_cards)-1}本）</span>'
                f'<span class="chev text-slate-400">▼</span></summary>'
                f'<div class="p-3.5 space-y-3 bg-white border-t border-slate-200">{extra_v}</div>'
                f'</details></div>'
            )

    # 切り抜きMarkdown（## 見出し単位で分割し、1つ目のみ表示・2つ目以降を折りたたみ）
    yt_path = ARTICLES_DIR / f"{slug}_youtube.md"
    clips_html = ""
    if yt_path.exists():
        raw_md = yt_path.read_text(encoding="utf-8")
        chunks: list[list[str]] = []
        cur: list[str] = []
        for line in raw_md.splitlines():
            if line.strip().startswith("## ") and cur and any(l.strip() and not l.strip().startswith("<!--") for l in cur):
                chunks.append(cur)
                cur = [line]
            else:
                cur.append(line)
        if cur:
            chunks.append(cur)

        rendered_chunks = [c for c in (_render_yt_chunk(ch, slug) for ch in chunks) if c.strip()]
        if rendered_chunks:
            first_clip = rendered_chunks[0]
            if len(rendered_chunks) == 1:
                clips_html = f'<div class="pt-2">{first_clip}</div>'
            else:
                rest_clips = '<hr class="my-5 border-slate-200" />'.join(rendered_chunks[1:])
                clips_html = (
                    f'<div class="pt-2 space-y-4">{first_clip}'
                    f'<details class="rounded-2xl border border-slate-200 bg-slate-50/70 overflow-hidden">'
                    f'<summary class="px-5 py-3.5 flex items-center justify-between text-sm font-semibold text-[#16284c] hover:bg-slate-100/70">'
                    f'<span>🎥 他のYouTube切り抜き・解説を読む（あと{len(rendered_chunks)-1}件）</span>'
                    f'<span class="chev text-slate-400">▼</span></summary>'
                    f'<div class="px-5 py-4 bg-white border-t border-slate-200">{rest_clips}</div>'
                    f'</details></div>'
                )

    # 全1,085ページ共通：公式YouTubeチャンネル内の疾患名検索ボタン
    q_name = re.sub(r"（.*?）|\(.*?\)", "", d["name"]).strip() or d["name"]
    search_url = f"{YT_CHANNEL_URL}/search?query={urllib.parse.quote(q_name)}"
    channel_footer = (
        f'<div class="pt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600 border-t border-slate-100">'
        f'<span>銀座そうぜん鍼灸院 公式YouTubeチャンネルで解説動画を配信中</span>'
        f'<a href="{e(search_url)}" target="_blank" rel="noopener" '
        f'class="inline-flex items-center gap-1.5 px-4 py-2 rounded-full bg-[#16284c] hover:bg-[#243b6b] text-white font-semibold transition-colors">'
        f'▶ 「{e(q_name)}」の動画をチャンネルで探す</a></div>'
    )

    badge = "公式動画＋切り抜き" if (videos_html and clips_html) else ("公式YouTube動画" if videos_html else ("院長切り抜き解説" if clips_html else "公式チャンネル連動"))

    return (
        f'<section id="sec-youtube" class="bg-white rounded-3xl border border-[#e8e3d9] shadow-sm p-6 sm:p-7 space-y-4">'
        f'<div class="flex items-center justify-between flex-wrap gap-2">'
        f'<div class="flex items-center gap-2">'
        f'<span class="w-1.5 h-5 bg-[#0ea5e9] rounded-full"></span>'
        f'<h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c]">院長YouTube解説・関連動画</h2>'
        f'</div>'
        f'<span class="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-[#eef7fc] text-[#0369a1] border border-[#bae6fd]">{badge}</span>'
        f'</div>'
        f'{videos_html}'
        f'{clips_html}'
        f'{channel_footer}'
        f'</section>'
    )


def render_category_cards(d: dict) -> str:
    """ツボ図解カード＋論文エビデンスカード（表・文献は折りたたみ収納）を生成する。"""
    ev = CATEGORY_EVIDENCE.get(d["category"])
    if not ev:
        return ""
    e = html.escape

    diag_file = ev.get("diagram", "")
    diag_html = ""
    if diag_file and (IMAGES_DIR / diag_file).exists():
        diag_html = (
            f'<figure class="rounded-2xl border border-slate-200 bg-[#faf8f4] p-3">'
            f'<img src="../images/diseases/{e(diag_file)}" alt="{e(d["category"])}の代表的なツボ位置図解" loading="lazy" class="w-full rounded-xl" />'
            f'<figcaption class="text-xs text-slate-500 mt-2.5 text-center">{e(ev.get("diagram_caption", ""))}</figcaption>'
            f'</figure>'
        )

    acu_rows = "".join(
        f'<tr><td class="p-3 font-bold text-[#16284c] whitespace-nowrap">{e(row[0])}</td><td class="p-3">{e(row[1])}</td><td class="p-3">{e(row[2])}</td><td class="p-3">{e(row[3])}</td></tr>'
        for row in ev.get("acupoints", [])
    )
    paper_rows = "".join(
        f'<tr><td class="p-3 font-bold text-[#16284c] whitespace-nowrap">{e(p[0])}</td><td class="p-3">{e(p[1])}</td><td class="p-3">{e(p[2])}</td>'
        f'<td class="p-3">{e(p[3])} <a href="{e(p[5])}" target="_blank" rel="noopener" class="text-[#16284c] underline">{e(p[4])}</a></td></tr>'
        for p in ev.get("papers", [])
    )

    ref_items = []
    if d.get("source_url") and d["source_url"].startswith("http"):
        ref_items.append(
            f'<li>銀座そうぜん鍼灸院「<a href="{e(d["source_url"])}" target="_blank" rel="noopener" class="text-[#16284c] underline">{e(d["name"])}の公式解説ページ</a>」</li>'
        )
    for p in ev.get("papers", []):
        ref_items.append(
            f'<li>{e(p[0])}（{e(p[1])}）— <a href="{e(p[5])}" target="_blank" rel="noopener" class="text-[#16284c] underline">{e(p[4])}</a></li>'
        )

    paper_cnt = len(ev.get("papers", []))

    return f'''
    <section id="sec-acupoints" class="bg-white rounded-3xl border border-[#e8e3d9] shadow-sm p-6 sm:p-7 space-y-5">
      <div class="flex items-center gap-2">
        <span class="w-1.5 h-5 bg-[#b68d40] rounded-full"></span>
        <h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c]">{e(d["category"])}のツボ図解とセルフケア</h2>
      </div>
      <p class="text-sm text-slate-600 leading-relaxed">
        実際の施術ではお一人おひとりの体質・症状に合わせて選穴しますが、{e(ev["domain_label"])}の臨床でよく用いられる代表的なツボの目安です。
      </p>
      {diag_html}
      <details class="rounded-2xl border border-slate-200 bg-slate-50/70 overflow-hidden">
        <summary class="px-5 py-3.5 flex items-center justify-between text-sm font-semibold text-[#16284c] hover:bg-slate-100/70 transition-colors">
          <span>📌 代表的なツボの詳細一覧表を開く（{len(ev.get("acupoints", []))}穴）</span>
          <span class="chev text-slate-400">▼</span>
        </summary>
        <div class="overflow-x-auto bg-white border-t border-slate-200">
          <table class="w-full min-w-[540px] text-xs sm:text-sm text-left border-collapse">
            <thead class="bg-[#16284c] text-white">
              <tr><th class="p-3">ツボ名</th><th class="p-3">位置の目安</th><th class="p-3">主な狙い</th><th class="p-3">セルフケアの注意点</th></tr>
            </thead>
            <tbody class="divide-y divide-slate-200 text-slate-700">{acu_rows}</tbody>
          </table>
        </div>
      </details>
    </section>

    <section id="sec-evidence" class="bg-white rounded-3xl border border-[#e8e3d9] shadow-sm p-6 sm:p-7 space-y-4">
      <div class="flex items-center justify-between flex-wrap gap-2">
        <div class="flex items-center gap-2">
          <span class="w-1.5 h-5 bg-[#16284c] rounded-full"></span>
          <h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c]">国際査読論文の科学的エビデンス</h2>
        </div>
        <span class="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-[#faf5eb] text-[#8c6239] border border-[#e5d5b5]">PubMed / Cochrane 準拠</span>
      </div>
      <p class="text-sm text-slate-600 leading-relaxed">
        国際的な医学データベース（PubMed・Cochrane等）で報告されている<strong>{e(ev["domain_label"])}領域の代表的な査読済み鍼灸研究</strong>をまとめています（標準治療との併用や研究上の限界を含めて正確に掲載）。
      </p>
      <details class="rounded-2xl border border-slate-200 bg-slate-50/70 overflow-hidden">
        <summary class="px-5 py-3.5 flex items-center justify-between text-sm font-semibold text-[#16284c] hover:bg-slate-100/70 transition-colors">
          <span>📚 査読論文データ・PubMed出典リンクを開く（{paper_cnt}件）</span>
          <span class="chev text-slate-400">▼</span>
        </summary>
        <div class="bg-white border-t border-slate-200 space-y-4 pb-4">
          <div class="overflow-x-auto">
            <table class="w-full min-w-[560px] text-xs sm:text-sm text-left border-collapse">
              <thead class="bg-[#16284c] text-white">
                <tr><th class="p-3">年・研究デザイン</th><th class="p-3">対象・規模</th><th class="p-3">主な結果</th><th class="p-3">限界・出典</th></tr>
              </thead>
              <tbody class="divide-y divide-slate-200 text-slate-700">{paper_rows}</tbody>
            </table>
          </div>
          <div class="px-5">
            <p class="text-xs font-bold text-[#16284c] mb-1.5">参考文献・出典</p>
            <ol class="list-decimal pl-5 text-xs text-slate-600 space-y-1">{"".join(ref_items)}</ol>
          </div>
        </div>
      </details>
      <div class="rounded-2xl bg-[#fffbeb] border-l-4 border-[#b68d40] px-4 py-3.5 text-xs sm:text-sm text-slate-700 leading-relaxed">
        <strong class="text-[#8c6239]">⚠ 受診を最優先すべきサイン：</strong>{e(ev["red_flags"])}
      </div>
    </section>
'''


def fold_tables_in_rich_html(rich_html: str) -> str:
    """個別詳細記事(articles/*.html)内の長いテーブルを自動で折りたたみ収納して見やすく整える。"""
    rich_html = rich_html.replace("<!-- YOUTUBE_SECTION -->", "").strip()

    def _wrap_table(match: re.Match) -> str:
        block = match.group(0)
        label = "📚 査読論文データ・出典表を開く" if ("研究デザイン" in block or "PubMed" in block or "PMID" in block) else "📌 詳細データ・ツボ一覧表を開く"
        return (
            f'<details class="my-4 rounded-2xl border border-slate-200 bg-slate-50/70 overflow-hidden">'
            f'<summary class="px-5 py-3.5 flex items-center justify-between text-sm font-semibold text-[#16284c] hover:bg-slate-100/70">'
            f'<span>{label}</span><span class="chev text-slate-400">▼</span></summary>'
            f'<div class="bg-white border-t border-slate-200 p-2">{block}</div></details>'
        )

    rich_html = re.sub(r'<div class="scroll-hint">.*?</div>\s*', "", rich_html, flags=re.DOTALL)
    rich_html = re.sub(r'<div class="table-wrap">.*?</div>', _wrap_table, rich_html, flags=re.DOTALL)
    return rich_html


def pick_related_diseases(d: dict, by_category: dict[str, list[dict]], limit: int = 6) -> list[dict]:
    peers = [p for p in by_category.get(d["category"], []) if p["slug"] != d["slug"]]
    if not peers:
        return []
    my_kws = {k.strip() for k in d["keywords"].split(",") if k.strip()}

    def score(p: dict) -> tuple[int, int, int, str]:
        p_kws = {k.strip() for k in p["keywords"].split(",") if k.strip()}
        return (1 if p["has_rich"] else 0, 1 if p["has_youtube"] else 0, len(my_kws & p_kws), p["id"])

    return sorted(peers, key=score, reverse=True)[:limit]


def render_related_html(related: list[dict], category: str) -> str:
    if not related:
        return ""
    e = html.escape
    cards = "".join(
        f'<a href="./{e(r["slug"])}.html" class="group block rounded-2xl border border-slate-200 bg-[#faf8f4] hover:bg-white hover:border-[#b68d40] p-3.5 transition-all">'
        f'<div class="flex items-center justify-between gap-2">'
        f'<span class="font-mincho font-bold text-sm text-[#16284c] group-hover:text-[#b68d40]">{e(r["name"])}</span>'
        f'<span class="text-xs text-slate-400 group-hover:text-[#b68d40] shrink-0">→</span></div>'
        f'<p class="text-xs text-slate-600 mt-1 line-clamp-2 leading-relaxed">{e(r["summary"])}</p>'
        f'</a>'
        for r in related
    )
    return (
        f'<section class="bg-white rounded-3xl border border-[#e8e3d9] shadow-sm p-6 sm:p-7 space-y-4">'
        f'<div class="flex items-center gap-2">'
        f'<span class="w-1.5 h-5 bg-[#b68d40] rounded-full"></span>'
        f'<h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c]">あわせて読まれる関連疾患（{e(category)}）</h2>'
        f'</div>'
        f'<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">{cards}</div>'
        f'</section>'
    )


RICH_ARTICLE_CSS = """
  html { scroll-behavior: smooth; }
  details > summary { list-style: none; cursor: pointer; }
  details > summary::-webkit-details-marker { display: none; }
  details[open] .chev { transform: rotate(180deg); }
  .chev { transition: transform 0.2s ease; }
  .rich-article { color: #1e293b; font-size: 15px; line-height: 1.88; }
  .rich-article h2 { font-family: 'Noto Serif JP', serif; font-size: clamp(1.15rem, 2.3vw, 1.4rem); font-weight: 700; color: #16284c; margin: 2rem 0 0.85rem; padding-bottom: 0.4rem; border-bottom: 1.5px solid #e8e3d9; line-height: 1.4; }
  .rich-article h2:first-child { margin-top: 0; }
  .rich-article h3 { font-family: 'Noto Serif JP', serif; font-size: clamp(1rem, 1.9vw, 1.12rem); font-weight: 700; color: #16284c; margin: 1.35rem 0 0.5rem; line-height: 1.45; }
  .rich-article p { margin: 0 0 0.9rem; color: #334155; }
  .rich-article a { color: #16284c; text-decoration: underline; text-underline-offset: 3px; }
  .rich-article a:hover { color: #b68d40; }
  .rich-article .verdict { background: #faf8f4; border-left: 4px solid #b68d40; border-radius: 0 16px 16px 0; padding: 16px 18px; margin: 0 0 20px; font-family: 'Noto Serif JP', serif; font-size: clamp(0.98rem, 1.9vw, 1.08rem); line-height: 1.78; color: #16284c; }
  .rich-article .verdict strong { color: #8c6239; }
  .rich-article .diagram-frame { margin: 18px 0; background: #faf8f4; border: 1px solid #e8e3d9; border-radius: 16px; padding: 12px; }
  .rich-article .diagram-frame img { display: block; width: 100%; height: auto; margin: 0 auto; border-radius: 10px; }
  .rich-article figcaption { font-size: 12px; color: #64748b; line-height: 1.6; margin-top: 8px; text-align: center; }
  .rich-article .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 10px; background: #ffffff; }
  .rich-article table { width: 100%; min-width: 540px; border-collapse: collapse; font-size: 13.5px; line-height: 1.65; }
  .rich-article thead th { background: #16284c; color: #ffffff; text-align: left; font-weight: 600; padding: 10px 12px; white-space: nowrap; font-size: 12.5px; }
  .rich-article tbody td { padding: 10px 12px; border-bottom: 1px solid #e2e8f0; vertical-align: top; color: #334155; }
  .rich-article tbody tr:last-child td { border-bottom: none; }
  .rich-article .col-primary { font-weight: 700; color: #16284c; white-space: nowrap; background: #f8fafc; }
  .rich-article .band-box { background: #faf8f4; border: 1px solid #e8e3d9; border-radius: 16px; padding: 18px; margin: 18px 0; }
  .rich-article .warn-box { background: #fffbeb; border-left: 4px solid #b68d40; border-radius: 0 14px 14px 0; padding: 14px 16px; margin: 16px 0; }
  .rich-article .pull-quote { border-left: 4px solid #16284c; padding: 12px 16px; margin: 20px 0; background: #faf5eb; border-radius: 0 14px 14px 0; font-family: 'Noto Serif JP', serif; color: #16284c; font-size: 15px; line-height: 1.8; }
  .rich-article .ref-list { padding-left: 1.25rem; font-size: 12.5px; color: #475569; line-height: 1.75; }
  .rich-article .ref-list li { margin-bottom: 6px; word-break: break-word; }
"""


CATEGORY_PALETTES = {
    "特に対応することが多い症状": ("#16284c", "#b68d40", "#faf5eb", "#243b6b"),
    "筋肉、骨のお悩み": ("#1b3a4b", "#c58b3c", "#f4f7f9", "#2a556c"),
    "神経のお悩み": ("#272643", "#b88a44", "#f5f4fa", "#3d3b66"),
    "脳神経のお悩み": ("#1e2a4a", "#c48f45", "#f3f6fc", "#304373"),
    "自律神経のお悩み": ("#1d3557", "#a9844f", "#f2f7fb", "#2c4e7c"),
    "耳鼻咽喉のお悩み": ("#1f3c58", "#c28b3b", "#f3f8fc", "#2f577e"),
    "消化器のお悩み": ("#21423a", "#c49342", "#f3faf7", "#326155"),
    "呼吸器のお悩み": ("#1d4455", "#b98a3f", "#f2f9fc", "#2c6178"),
    "女性のお悩み": ("#4a2545", "#c98b5b", "#fcf4f8", "#6b3764"),
    "眼のお悩み": ("#1b3b5c", "#4f86b8", "#f2f7fc", "#2b5887"),
    "皮膚のお悩み": ("#3c3a2a", "#c28f45", "#faf8f2", "#59563f"),
    "子供のお悩み": ("#244b5a", "#d19344", "#f4fafb", "#356c80"),
    "循環器のお悩み": ("#4b2230", "#c48a47", "#fcf4f6", "#6d3448"),
    "泌尿器のお悩み": ("#223c4e", "#b58b45", "#f3f7fa", "#345973"),
    "その他のお悩み": ("#263445", "#b68d40", "#f6f7f9", "#3c5069"),
}


def _wrap_ja_title(text: str, max_chars: int = 12) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    lines = []
    cur = text
    while len(cur) > max_chars and len(lines) < 2:
        lines.append(cur[:max_chars])
        cur = cur[max_chars:]
    if cur:
        if len(cur) > max_chars:
            cur = cur[:max_chars - 1] + "…"
        lines.append(cur)
    return lines


def generate_unique_card_svg(d: dict) -> str:
    e = html.escape
    primary, accent, _, secondary = CATEGORY_PALETTES.get(
        d["category"], ("#16284c", "#b68d40", "#faf5eb", "#243b6b")
    )
    h_bytes = hashlib.sha256(f"{d['id']}:{d['slug']}:{d['name']}".encode("utf-8")).digest()
    seed0, seed1, seed2, seed3 = h_bytes[0], h_bytes[1], h_bytes[2], h_bytes[3]

    cx1 = 440 + (seed0 % 140)
    cy1 = 40 + (seed1 % 110)
    r1 = 75 + (seed2 % 55)
    cx2 = 480 + (seed3 % 110)
    cy2 = 210 + (seed0 % 100)
    r2 = 55 + (seed1 % 45)
    wave_y = 250 + (seed2 % 35)
    motif_variant = seed3 % 4

    if motif_variant == 0:
        motif = (
            f'<circle cx="{cx1}" cy="{cy1}" r="{r1}" fill="{accent}" fill-opacity="0.16" />'
            f'<circle cx="{cx2}" cy="{cy2}" r="{r2}" fill="{secondary}" fill-opacity="0.28" />'
            f'<circle cx="{cx1}" cy="{cy1}" r="{int(r1*0.62)}" fill="none" stroke="{accent}" stroke-opacity="0.35" stroke-width="1.5" />'
        )
    elif motif_variant == 1:
        motif = (
            f'<path d="M0 {wave_y} Q 160 {wave_y-38}, 320 {wave_y-8} T 640 {wave_y-22} L 640 360 L 0 360 Z" fill="{secondary}" fill-opacity="0.26" />'
            f'<circle cx="{cx1}" cy="{cy1}" r="{r1}" fill="none" stroke="{accent}" stroke-opacity="0.32" stroke-width="2" stroke-dasharray="6 6" />'
        )
    elif motif_variant == 2:
        motif = (
            f'<rect x="{420 + (seed0 % 80)}" y="{30 + (seed1 % 50)}" width="{110 + (seed2 % 40)}" height="{110 + (seed2 % 40)}" rx="24" transform="rotate({12 + (seed3 % 25)} 500 110)" fill="{accent}" fill-opacity="0.15" />'
            f'<circle cx="{cx2}" cy="{cy2}" r="{r2}" fill="{secondary}" fill-opacity="0.30" />'
        )
    else:
        motif = (
            f'<path d="M{390 + (seed0 % 50)} 0 L640 0 L640 360 L{470 + (seed1 % 60)} 360 Z" fill="{secondary}" fill-opacity="0.28" />'
            f'<circle cx="{cx1}" cy="{cy1}" r="{int(r1*0.85)}" fill="{accent}" fill-opacity="0.18" />'
        )

    title_lines = _wrap_ja_title(d["name"], max_chars=12)
    font_size = 38 if len(title_lines) == 1 and len(title_lines[0]) <= 8 else (32 if len(title_lines) == 1 else 30)
    y_start = 164 if len(title_lines) == 1 else 148
    title_svg = "".join(
        f'<text x="44" y="{y_start + i * 42}" font-family="Noto Serif JP, Hiragino Mincho ProN, serif" '
        f'font-size="{font_size}" font-weight="700" fill="#ffffff">{e(line)}</text>'
        for i, line in enumerate(title_lines)
    )

    kws = [k.strip() for k in (d.get("keywords") or "").split(",") if k.strip() and k.strip() != d["name"]][:3]
    pills = []
    px = 44
    py = 258
    for kw in kws:
        label = kw[:10] + ("…" if len(kw) > 10 else "")
        w = max(72, len(label) * 14 + 26)
        if px + w > 596:
            break
        pills.append(
            f'<rect x="{px}" y="{py}" width="{w}" height="28" rx="14" fill="#ffffff" fill-opacity="0.14" stroke="#ffffff" stroke-opacity="0.25" />'
            f'<text x="{px + w/2:.1f}" y="{py + 18}" text-anchor="middle" font-family="Noto Sans JP, sans-serif" font-size="12.5" font-weight="600" fill="#f8fafc">#{e(label)}</text>'
        )
        px += w + 10
    pills_svg = "".join(pills)

    cat_label = d["category"]
    cat_w = max(120, len(cat_label) * 14 + 34)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360" width="640" height="360" role="img" aria-label="{e(d['name'])}の鍼灸解説">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{primary}" />
      <stop offset="100%" stop-color="{secondary}" />
    </linearGradient>
  </defs>
  <rect width="640" height="360" fill="url(#bg)" />
  {motif}
  <rect x="0" y="0" width="12" height="360" fill="{accent}" />
  <rect x="44" y="42" width="{cat_w}" height="30" rx="15" fill="{accent}" />
  <text x="{44 + cat_w/2:.1f}" y="62" text-anchor="middle" font-family="Noto Sans JP, sans-serif" font-size="13" font-weight="700" fill="#ffffff">{e(cat_label)}</text>
  <text x="596" y="62" text-anchor="end" font-family="Noto Sans JP, sans-serif" font-size="13" font-weight="600" fill="#e2e8f0" fill-opacity="0.85">{e(d["id"])}</text>
  {title_svg}
  <line x1="44" y1="226" x2="180" y2="226" stroke="{accent}" stroke-width="3" stroke-linecap="round" />
  {pills_svg}
  <text x="44" y="330" font-family="Noto Serif JP, serif" font-size="13" font-weight="600" fill="#e2e8f0" fill-opacity="0.9">銀座そうぜん鍼灸院グループ｜症状・疾患解説</text>
</svg>
"""



CATEGORY_MECHANISMS = {
    "特に対応することが多い症状": {
        "title": "脳の過覚醒鎮静と深層筋・自律神経の連動メカニズム",
        "steps": [
            ("STEP 1", "経穴・深層筋への繊細な刺激", "首肩や手足の経穴に痛みの少ない微細刺激を入れ、末梢の感覚受容器へ信号を送ります。"),
            ("STEP 2", "脊髄・脳幹（孤束核）へ伝達", "刺激信号が脊髄・脳幹へ伝わり、過剰に高ぶった交感神経の興奮ブレーキを作動させます。"),
            ("STEP 3", "大脳・自律神経の過覚醒を鎮静", "視床下部や大脳皮質の過敏性を穏やかに鎮め、深層筋の緊張と局所血流の悪循環を断ちます。"),
            ("STEP 4", "神経可塑性による安定定着", "体の土台が整った段階で施術間隔を空け、症状が出にくい静かな状態の定着を促します。"),
        ],
    },
    "筋肉、骨のお悩み": {
        "title": "トリガーポイント解放と疼痛抑制系の作用機序",
        "steps": [
            ("STEP 1", "深層筋の硬結へ直接アプローチ", "手技では届きにくい深層筋の硬結（トリガーポイント）へ繊細な鍼で直接刺激を届けます。"),
            ("STEP 2", "軸索反射による局所血流改善", "CGRPや一酸化窒素の放出により微小血管が拡張し、滞っていた発痛物質の洗い流しを促します。"),
            ("STEP 3", "脊髄ゲート制御＋下行性疼痛抑制", "脊髄後角で痛み信号を遮断し、脳幹からの内因性鎮痛物質（エンドルフィン等）の働きを高めます。"),
            ("STEP 4", "筋柔軟性の回復と再発予防", "防御性の筋スパズムが解け、関節可動域と本来の動きやすさが段階的に回復します。"),
        ],
    },
    "神経のお悩み": {
        "title": "末梢神経の微小循環改善と中枢性感作の鎮静機序",
        "steps": [
            ("STEP 1", "該当神経走行・遠位経穴の調整", "過敏な皮膚への過剰刺激を避けつつ、神経の走行に沿った安全な部位と遠位穴を刺激します。"),
            ("STEP 2", "神経周囲の微小血流・栄養促進", "神経を圧迫する周囲筋の過緊張を緩め、神経線維の修復に必要な酸素と血流環境を整えます。"),
            ("STEP 3", "脊髄・脳の疼痛過敏（中枢性感作）抑制", "微細な刺激で過敏化した痛み伝達回路を鎮静し、ピリピリ感や放散痛の波を和らげます。"),
            ("STEP 4", "感覚と生活機能の安定化", "睡眠不足や自律神経の緊張も同時に整え、しびれ・神経痛による生活支障度を軽減します。"),
        ],
    },
    "脳神経のお悩み": {
        "title": "脳循環・三叉神経血管系と神経可塑性の調整機序",
        "steps": [
            ("STEP 1", "頭部・後頸部・四肢経穴への刺激", "百会・風池・合谷などの経穴刺激が三叉神経や大後頭神経・体性感覚路へ穏やかに作用します。"),
            ("STEP 2", "脳幹・視床下部の神経伝達調整", "脳幹の自律神経中枢やセロトニン・GABA系のバランスを整え、神経の過敏発火を鎮めます。"),
            ("STEP 3", "頭頸部の微小循環と筋緊張緩和", "後頭下筋群や首深部のこわばりを解き、頭部・内耳・顔面周囲の血流環境をサポートします。"),
            ("STEP 4", "標準治療・リハビリ効果の後押し", "神経回路の再学習（可塑性）と睡眠の質を高め、専門医の治療やリハビリの土台を支えます。"),
        ],
    },
    "自律神経のお悩み": {
        "title": "体性-自律神経反射による交感・副交感神経の切替機序",
        "steps": [
            ("STEP 1", "体表の緊張サイン評価と低刺激刺鍼", "首肩・みぞおち・手足の過緊張ポイントへ、交感神経を刺激しすぎない優しい刺激を行います。"),
            ("STEP 2", "迷走神経（副交感神経）の活性化", "体性-自律神経反射を通じて脳幹の迷走神経核へ働きかけ、心拍変動（HF/SDNN）を整えます。"),
            ("STEP 3", "ストレス応答系（HPA軸）の鎮静", "過剰なコルチゾール分泌や過覚醒状態を落ち着かせ、深い呼吸と入眠スイッチを引き出します。"),
            ("STEP 4", "自律神経の自然な切り替え回復", "「活動（昼）」と「休息（夜）」のメリハリが戻り、動悸・不眠・倦怠感の波が穏やかになります。"),
        ],
    },
    "耳鼻咽喉のお悩み": {
        "title": "耳周囲・頸部の微小循環改善と聴覚・粘膜調整機序",
        "steps": [
            ("STEP 1", "耳周囲・後頸部・遠位経穴の調整", "翳風・風池・合谷など、耳鼻咽喉領域の神経・血管と連動する経穴へ繊細にアプローチします。"),
            ("STEP 2", "内耳・鼻咽腔の微小循環サポート", "首・顎まわりの深層筋緊張を緩め、内耳や粘膜周囲の滞った血流とリンパ循環を促します。"),
            ("STEP 3", "自律神経と聴覚・粘膜過敏の鎮静", "ストレスや睡眠不足で高ぶった交感神経を鎮め、音への過敏反応や粘膜の過剰反応を和らげます。"),
            ("STEP 4", "耳鼻科治療と併用した回復促進", "標準治療を妨げない設計で、生活支障度（THI等）の軽減と再発しにくい体調づくりを支えます。"),
        ],
    },
    "消化器のお悩み": {
        "title": "脳腸相関と迷走神経反射による胃腸運動の調整機序",
        "steps": [
            ("STEP 1", "足三里・内関・天枢への経穴刺激", "胃腸機能と深く結びつく四肢・腹部の経穴を刺激し、体性感覚神経から中枢へ信号を送ります。"),
            ("STEP 2", "迷走神経遠心路の活性化", "脳幹を経由した体性-内臓反射により、副交感神経（迷走神経）経由で胃腸の働きを調整します。"),
            ("STEP 3", "胃排出・腸管蠕動と内臓知覚の正常化", "弱まったぜん動を促す一方、下痢型の過剰な収縮や内臓知覚過敏は穏やかに鎮静します。"),
            ("STEP 4", "脳腸相関の安定と食後・排便リズム回復", "ストレスで乱れにくい胃腸の土台を整え、胃もたれ・便通異常・吐き気の軽減を目指します。"),
        ],
    },
    "女性のお悩み": {
        "title": "視床下部-下垂体-卵巣（HPO）軸と骨盤内血流の調整機序",
        "steps": [
            ("STEP 1", "三陰交・腰仙部（八髎穴）への優しい刺激", "骨盤内臓器と同じ脊髄分節を共有する腰仙部や下肢経穴へ、体質に合わせた刺激を行います。"),
            ("STEP 2", "骨盤内微小循環と冷えの改善", "骨盤周囲の自律神経反射を介して子宮・卵巣周囲の血流を促し、冷えやうっ血を和らげます。"),
            ("STEP 3", "視床下部（自律神経・体温中枢）の安定", "ホルモン変動で過敏になった視床下部を鎮静し、ほてり・発汗・気分の揺らぎを穏やかにします。"),
            ("STEP 4", "周期・ライフステージに寄り添う体調維持", "婦人科治療とも協調しながら、月経・妊娠・更年期それぞれの負担を軽くする土台を整えます。"),
        ],
    },
    "がんの種類": {
        "title": "迷走神経抗炎症反射と標準治療を支える支持療法機序",
        "steps": [
            ("STEP 1", "安全領域（遠位経穴）への低刺激アプローチ", "手術創・リンパ節郭清側・患部を厳格に避け、内関・足三里・合谷など安全な経穴を刺激します。"),
            ("STEP 2", "中枢性鎮痛・制吐経路の活性化", "内因性鎮痛系や延髄嘔吐中枢の抑制経路へ働きかけ、痛み・吐き気・しびれの軽減を助けます。"),
            ("STEP 3", "迷走神経抗炎症反射と自律神経調整", "治療に伴う炎症性サイトカイン反応や過覚醒を穏やかに整え、倦怠感や不眠の負担を和らげます。"),
            ("STEP 4", "標準がん治療を完遂しやすいQOL維持", "主治医の治療計画を最優先に、体力と生活の質を保ちながら治療を続けられる環境を支えます。"),
        ],
    },
    "皮膚のお悩み": {
        "title": "神経性炎症・掻痒（かゆみ）伝達と自律神経の鎮静機序",
        "steps": [
            ("STEP 1", "皮疹を避けた安全な経穴刺激", "湿疹・傷口への直接刺激を避け、曲池・血海・築賓・三陰交など全身の調整穴へアプローチします。"),
            ("STEP 2", "末梢・中枢の掻痒（かゆみ）シグナル抑制", "痒み感覚を伝える神経経路の過敏性を鎮め、「かゆい→掻く→悪化」の悪循環を断ちます。"),
            ("STEP 3", "自律神経と末梢循環・皮膚バリアの支援", "交感神経の過緊張や夜間覚醒を和らげ、皮膚の修復が進みやすい血流と睡眠環境を整えます。"),
            ("STEP 4", "皮膚科治療と併用した再燃しにくい体質へ", "標準の保湿・外用・内服治療と協調し、ストレスや季節変化で悪化しにくい土台を支えます。"),
        ],
    },
    "循環器のお悩み": {
        "title": "心拍変動・血管トーンを整える自律神経サポート機序",
        "steps": [
            ("STEP 1", "内関（PC6）・神門など心包経・心経の調整", "循環器専門医の標準治療を絶対前提とし、手首や背部の安全な経穴へ低刺激でアプローチします。"),
            ("STEP 2", "交感神経過剰興奮の抑制と迷走神経調整", "心臓への過剰な交感神経刺激を穏やかに鎮め、心拍リズムと血管緊張の急変を抑えます。"),
            ("STEP 3", "胸郭・背部の筋緊張緩和と深い呼吸", "ストレスで固まりやすい胸郭や横隔膜の緊張を解き、酸素摂取とリラックス反応を促します。"),
            ("STEP 4", "標準治療下での発作不安・睡眠の質改善", "内服管理を厳守しながら、疲労やストレスが引き金となる症状不安の軽減を支えます。"),
        ],
    },
    "泌尿器のお悩み": {
        "title": "仙髄排尿反射（陰部神経）と骨盤底の協調調整機序",
        "steps": [
            ("STEP 1", "腰仙部（八髎穴周囲）と下肢経穴の調整", "膀胱・尿道括約筋を支配する仙髄神経（S2〜S4）領域の経穴へ的確にアプローチします。"),
            ("STEP 2", "陰部神経・骨盤神経の反射機能調整", "弱まった骨盤底筋の収縮サポートと、過敏になった膀胱排尿筋の鎮静をバランスよく促します。"),
            ("STEP 3", "骨盤内血流改善と冷え・緊張の緩和", "腰殿部と下腹部の血流を高め、冷えや過緊張による頻尿・違和感・痛みを和らげます。"),
            ("STEP 4", "骨盤底筋訓練・標準治療との相乗サポート", "泌尿器科の評価やセルフエクササイズと併用し、安心して外出できる日常の回復を支えます。"),
        ],
    },
    "眼のお悩み": {
        "title": "後頸部〜眼周囲の微小循環と毛様体筋・自律神経調整機序",
        "steps": [
            ("STEP 1", "後頸部・側頭部・遠位経穴への安全アプローチ", "眼球周囲の危険な深刺しを避け、風池・天柱・合谷・太衝など安全な経穴へ繊細に施術します。"),
            ("STEP 2", "頭頸部・眼周囲の血流と筋緊張緩和", "PC・スマホ作業や緊張で固まった首深層筋と眼輪筋周囲の血流を促し、重だるさを軽減します。"),
            ("STEP 3", "副交感神経の調整とピント疲労ケア", "交感神経優位による目の乾き・ピント調節疲労を鎮め、涙液分泌とリラックス反応を助けます。"),
            ("STEP 4", "眼科標準治療を前提とした視覚負担軽減", "眼科の点眼・定期検査を最優先としつつ、日々の眼精疲労や頭痛・肩こりの連鎖を整えます。"),
        ],
    },
    "子供のお悩み": {
        "title": "「刺さない小児はり」による皮膚感覚〜自律神経の鎮静機序",
        "steps": [
            ("STEP 1", "皮膚を優しく撫でる無痛アプローチ", "刺さない専用の「小児はり（てい鍼）」で、背中や手足の皮膚をリズミカルに優しく撫でます。"),
            ("STEP 2", "C触覚線維を介した心地よい神経伝達", "皮膚への穏やかな触覚刺激が脳へ伝わり、安心ホルモン（オキシトシン等）と副交感神経を促します。"),
            ("STEP 3", "未熟な自律神経・睡眠・胃腸リズムの安定", "興奮しやすい子どもの神経バランスを落ち着かせ、夜泣き・おねしょ・起立不調の土台を整えます。"),
            ("STEP 4", "小児科・療育と連携した健やかな成長支援", "保護者の方と寄り添いながら、お子さまの体調と成長ペースに合わせた無理のないケアを続けます。"),
        ],
    },
    "内分泌のお悩み": {
        "title": "神経-内分泌-免疫ネットワークと末梢循環の調整機序",
        "steps": [
            ("STEP 1", "全身の経穴による体質・緊張バランス評価", "内科専門医の薬物・食事管理を前提に、足三里・三陰交・背部経穴へ安全にアプローチします。"),
            ("STEP 2", "視床下部・自律神経を介した代謝環境調整", "ストレス由来の交感神経過緊張を鎮め、ホルモン分泌や血糖・尿酸管理を乱す疲労要因を軽減します。"),
            ("STEP 3", "末梢微小循環と胃腸・神経機能のサポート", "手足や内臓の血流を促し、合併しやすいしびれ・胃もたれ・倦怠感・関節痛の負担を和らげます。"),
            ("STEP 4", "専門医管理と協調した長期コンディション維持", "皮膚衛生や低刺激を徹底し、日々の生活習慣改善を続けやすい安定した体調づくりを支えます。"),
        ],
    },
}



CATEGORY_MECH_IMAGE = {
    "特に対応することが多い症状": "mech_brain_autonomic.jpg",
    "脳神経のお悩み": "mech_brain_autonomic.jpg",
    "自律神経のお悩み": "mech_brain_autonomic.jpg",
    "耳鼻咽喉のお悩み": "mech_brain_autonomic.jpg",
    "眼のお悩み": "mech_brain_autonomic.jpg",
    "筋肉、骨のお悩み": "mech_muscle_nerve.jpg",
    "神経のお悩み": "mech_muscle_nerve.jpg",
    "消化器のお悩み": "mech_digestive_vagus.jpg",
    "呼吸器のお悩み": "mech_digestive_vagus.jpg",
    "女性のお悩み": "mech_women_endocrine.jpg",
    "内分泌のお悩み": "mech_women_endocrine.jpg",
    "泌尿器のお悩み": "mech_women_endocrine.jpg",
    "がんの種類": "mech_cancer_support.jpg",
    "循環器のお悩み": "mech_cancer_support.jpg",
    "皮膚のお悩み": "mech_cancer_support.jpg",
    "子供のお悩み": "mech_pediatric_shonishin.jpg",
}


def generate_mechanism_svg(d: dict) -> str:
    """各疾患ページ専用の「鍼灸が働く4ステップ作用機序」図解SVGを自動生成する。"""
    e = html.escape
    mech = CATEGORY_MECHANISMS.get(d["category"], CATEGORY_MECHANISMS["特に対応することが多い症状"])
    primary, accent, _, secondary = CATEGORY_PALETTES.get(
        d["category"], ("#16284c", "#b68d40", "#faf5eb", "#243b6b")
    )
    steps = mech["steps"]
    boxes = []
    xs = [24, 212, 400, 588]
    for idx, (tag, head, desc) in enumerate(steps):
        x = xs[idx]
        # Wrap head to max 11 chars per line, desc to 3 short lines
        h_lines = [head[:11], head[11:22]] if len(head) > 11 else [head]
        d_lines = [desc[i:i+13] for i in range(0, min(len(desc), 39), 13)]
        if len(desc) > 39:
            d_lines[-1] = d_lines[-1][:-1] + "…"
        h_svg = "".join(
            f'<text x="{x+14}" y="{114 + i*18}" font-family="Noto Serif JP, serif" font-size="13.5" font-weight="700" fill="{primary}">{e(ln)}</text>'
            for i, ln in enumerate(h_lines)
        )
        d_y0 = 152 if len(h_lines) > 1 else 138
        d_svg = "".join(
            f'<text x="{x+14}" y="{d_y0 + i*17}" font-family="Noto Sans JP, sans-serif" font-size="11" fill="#475569">{e(ln)}</text>'
            for i, ln in enumerate(d_lines)
        )
        boxes.append(
            f'<rect x="{x}" y="68" width="168" height="156" rx="16" fill="#ffffff" stroke="#e2ddd2" stroke-width="1.5" />'
            f'<rect x="{x+14}" y="80" width="56" height="20" rx="10" fill="{accent if idx in (0,3) else primary}" />'
            f'<text x="{x+42}" y="94" text-anchor="middle" font-family="Noto Sans JP, sans-serif" font-size="10.5" font-weight="700" fill="#ffffff">{e(tag)}</text>'
            f'{h_svg}{d_svg}'
        )
        if idx < 3:
            ax = x + 173
            boxes.append(
                f'<path d="M{ax} 146 L{ax+10} 146 M{ax+6} 141 L{ax+11} 146 L{ax+6} 151" '
                f'stroke="{accent}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none" />'
            )

    title_str = f"{d['name']}における鍼灸の作用機序（4ステップ）"
    if len(title_str) > 34:
        title_str = f"{d['name'][:18]}…の鍼灸作用機序"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 780 250" width="780" height="250" role="img" aria-label="{e(title_str)}">
  <rect width="780" height="250" rx="20" fill="#faf8f4" />
  <rect x="0" y="0" width="780" height="48" rx="20" fill="{primary}" />
  <rect x="0" y="28" width="780" height="20" fill="{primary}" />
  <text x="24" y="31" font-family="Noto Serif JP, serif" font-size="15.5" font-weight="700" fill="#ffffff">🔬 {e(title_str)}</text>
  <text x="756" y="30" text-anchor="end" font-family="Noto Sans JP, sans-serif" font-size="11" fill="#e2e8f0">銀座そうぜん鍼灸院</text>
  {"".join(boxes)}
</svg>
"""


def render_mechanism_block(d: dict) -> str:
    """概要・改善の流れの中に自然に組み込まれる作用機序（イラスト＋4ステップ解説）ブロックを生成する。"""
    e = html.escape
    mech = CATEGORY_MECHANISMS.get(d["category"], CATEGORY_MECHANISMS["特に対応することが多い症状"])
    mech_img_file = CATEGORY_MECH_IMAGE.get(d["category"], "mech_brain_autonomic.jpg")
    mech_img_src = f"../images/diseases/{mech_img_file}" if (IMAGES_DIR / mech_img_file).exists() else f"./mechanisms/{d['slug']}.svg"
    step_items = "".join(
        f'<div class="rounded-2xl bg-white border border-[#e8e3d9] p-3.5">'
        f'<div class="flex items-center gap-2 mb-1">'
        f'<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-[#16284c] text-white">{e(st[0])}</span>'
        f'<span class="font-bold text-xs sm:text-sm text-[#16284c]">{e(st[1])}</span>'
        f'</div>'
        f'<p class="text-xs text-slate-600 leading-relaxed !mb-0">{e(st[2])}</p>'
        f'</div>'
        for st in mech["steps"]
    )
    return (
        f'<div id="sec-mechanism" class="my-6 rounded-2xl bg-[#faf8f4] border border-[#e8e3d9] p-4 sm:p-5 space-y-4">'
        f'<div class="flex items-center justify-between flex-wrap gap-2">'
        f'<div class="flex items-center gap-2">'
        f'<span class="w-1.5 h-5 bg-[#b68d40] rounded-full inline-block"></span>'
        f'<h3 class="font-mincho text-base sm:text-lg font-bold text-[#16284c] !m-0">なぜ鍼灸で改善へ向かうのか？（作用機序の図解）</h3>'
        f'</div>'
        f'<span class="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-white text-[#8c6239] border border-[#e5d5b5]">神経生理学的メカニズム</span>'
        f'</div>'
        f'<p class="text-xs sm:text-sm text-slate-600 leading-relaxed !mb-0">{e(d["name"])}に対する鍼灸は、<strong>{e(mech["title"])}</strong>を通じて、お体本来の回復力を引き出します。</p>'
        f'<figure class="rounded-2xl border border-[#e8e3d9] bg-white p-2 overflow-hidden !my-2">'
        f'<img src="{e(mech_img_src)}" alt="{e(d["name"])}に対する鍼灸の作用機序イラスト" loading="lazy" class="w-full h-auto rounded-xl" />'
        f'<figcaption class="text-xs text-slate-500 mt-2 text-center">図：{e(d["name"])}に対する鍼灸の作用機序（{e(mech["title"])}）</figcaption>'
        f'</figure>'
        f'<div class="grid grid-cols-1 sm:grid-cols-2 gap-2.5">{step_items}</div>'
        f'</div>'
    )


def inject_mechanism_into_rich_html(rich_html: str, mech_block: str) -> str:
    """個別詳細記事(articles/*.html)内の「改善の流れ・概要」の直後（ツボ見出しの直前）に作用機序ブロックを挿入する。"""
    # 「ツボ」を含む <h2> の直前に挿入（頭鳴り等では「改善の流れ」の直後になる）
    m = re.search(r'<h2[^>]*>[^<]*ツボ[^<]*</h2>', rich_html)
    if m:
        return rich_html[:m.start()] + mech_block + "\n" + rich_html[m.start():]
    # 該当がなければ2つ目の <h2> の直前に挿入
    matches = list(re.finditer(r'<h2[^>]*>', rich_html))
    if len(matches) >= 2:
        pos = matches[1].start()
        return rich_html[:pos] + mech_block + "\n" + rich_html[pos:]
    return rich_html + "\n" + mech_block


def render_detail_html(d: dict, by_category: dict[str, list[dict]]) -> str:
    e = html.escape
    symptoms = split_items(d["symptoms"])
    approaches = split_items(d["approach"])
    faqs = parse_faq(d["faq"])
    tags = [t.strip() for t in d["keywords"].split(",") if t.strip()][:6]
    canonical_url = f"{SITE_BASE_URL}/disease/{d['slug']}.html"
    og_image = f"{SITE_BASE_URL}/images/diseases/{d['image_file']}" if d["image_exists"] else ""

    rich_path = ARTICLES_DIR / f"{d['slug']}.html"
    rich_html = fold_tables_in_rich_html(rich_path.read_text(encoding="utf-8")) if rich_path.exists() else ""
    yt_card_html = render_youtube_section_card(d)

    ld_blocks = []
    medical_ld = {
        "@context": "https://schema.org",
        "@type": "MedicalWebPage",
        "name": f"{d['name']}の鍼灸｜銀座そうぜん鍼灸院グループ",
        "description": d["meta_description"],
        "url": canonical_url,
        "inLanguage": "ja",
        "about": {"@type": "MedicalCondition", "name": d["name"]},
        "publisher": {"@type": "MedicalClinic", "name": "銀座そうぜん鍼灸院グループ", "url": PORTAL_URL},
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

    # よくある質問（アコーディオンカード）
    faq_card_html = ""
    if faqs:
        faq_items = []
        for idx, f in enumerate(faqs):
            open_attr = " open" if idx == 0 else ""
            faq_items.append(
                f'<details class="rounded-2xl border border-slate-200 bg-[#faf8f4] overflow-hidden"{open_attr}>'
                f'<summary class="px-5 py-3.5 flex items-center justify-between text-sm font-semibold text-[#16284c]">'
                f'<span>Q. {e(f["q"])}</span><span class="chev text-slate-400">▼</span></summary>'
                f'<div class="px-5 pb-4 pt-3 text-sm text-slate-600 leading-relaxed border-t border-slate-200/70 bg-white">{e(f["a"])}</div>'
                f'</details>'
            )
        faq_card_html = (
            f'<section id="sec-faq" class="bg-white rounded-3xl border border-[#e8e3d9] shadow-sm p-6 sm:p-7 space-y-3">'
            f'<div class="flex items-center gap-2 mb-1"><span class="w-1.5 h-5 bg-[#16284c] rounded-full"></span>'
            f'<h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c]">よくある質問（FAQ）</h2></div>'
            f'{"".join(faq_items)}</section>'
        )

    tags_html = "".join(f'<span class="px-3 py-1 rounded-full bg-white border border-[#e8e3d9] text-slate-600 text-xs">#{e(t)}</span>' for t in tags)
    related_html = render_related_html(pick_related_diseases(d, by_category, limit=6), d["category"])

    mech_block_html = render_mechanism_block(d)
    if rich_html:
        rich_with_mech = inject_mechanism_into_rich_html(rich_html, mech_block_html)
        content_cards = (
            f'{yt_card_html}'
            f'<section id="sec-acupoints" class="bg-white rounded-3xl border border-[#e8e3d9] shadow-sm p-6 sm:p-7">'
            f'<div class="rich-article">{rich_with_mech}</div>'
            f'</section>'
        )
    else:
        src_link = (
            f'（<a href="{e(d["source_url"])}" target="_blank" rel="noopener" class="text-[#16284c] underline">公式解説</a>）'
            if d.get("source_url") and d["source_url"].startswith("http") else ""
        )
        sym_lis = "".join(
            f'<li class="flex items-start gap-2 text-slate-700 text-sm"><span class="text-[#b68d40] font-bold">✓</span><span>{e(s)}</span></li>'
            for s in symptoms
        )
        app_cards = "".join(
            f'<div class="rounded-2xl bg-[#faf8f4] border border-[#ece6da] p-4 text-sm text-slate-700 leading-relaxed">{e(a)}</div>'
            for a in approaches
        )
        overview_card = (
            f'<section class="bg-white rounded-3xl border border-[#e8e3d9] shadow-sm p-6 sm:p-7 space-y-5">'
            f'<div class="flex items-center gap-2"><span class="w-1.5 h-5 bg-[#b68d40] rounded-full"></span>'
            f'<h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c]">まず知っておきたい要点と改善の流れ</h2></div>'
            f'<p class="text-sm sm:text-[15px] text-slate-700 leading-[1.85]">{e(d["mechanism"])}{src_link}</p>'
            f'{f"""<div class="rounded-2xl bg-[#faf8f4] border border-[#ece6da] p-4 sm:p-5"><p class="text-xs font-bold text-[#8c6239] mb-2.5">このようなお悩みはありませんか？</p><ul class="space-y-2">{sym_lis}</ul></div>""" if sym_lis else ""}'
            f'{f"""<div class="space-y-2.5"><p class="text-xs font-bold text-[#16284c]">当院の施術アプローチ・改善の流れ</p>{app_cards}</div>""" if app_cards else ""}'
            f'{mech_block_html}'
            f'</section>'
        )
        content_cards = overview_card + "\n" + yt_card_html + "\n" + render_category_cards(d)

    yt_badge_hero = '<span class="px-3 py-1 rounded-full text-[11px] font-medium bg-white/15 text-white/95">🎥 院長YouTube解説あり</span>' if d["has_youtube"] else ""

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
  <style>body {{ font-family: 'Noto Sans JP', sans-serif; background-color: #f6f4ef; color: #1e293b; }} .font-mincho {{ font-family: 'Noto Serif JP', serif; }}{RICH_ARTICLE_CSS}</style>
  {structured_ld_html}
</head>
<body class="min-h-screen antialiased pb-24 sm:pb-12">
  <div class="max-w-3xl mx-auto px-3.5 sm:px-5 py-6 sm:py-9 space-y-6">
    <nav class="text-xs text-slate-500 flex flex-wrap items-center gap-2">
      <a href="{e(PORTAL_URL)}" class="hover:text-[#16284c] underline">総合ポータル</a><span>/</span>
      <a href="./index.html" class="hover:text-[#16284c] underline">症状・疾患から探す</a><span>/</span>
      <span class="text-[#16284c] font-medium">{e(d["name"])}</span>
    </nav>

    <header class="bg-gradient-to-br from-[#16284c] via-[#1d3563] to-[#233d70] text-white rounded-3xl px-6 sm:px-8 py-7 sm:py-8 shadow-sm">
      <div class="flex flex-wrap items-center gap-2 mb-3">
        <span class="px-3 py-1 rounded-full text-[11px] font-semibold bg-[#b68d40] text-white">{e(d["category"])}</span>
        {yt_badge_hero}
      </div>
      <h1 class="font-mincho text-2xl sm:text-3xl font-bold leading-snug tracking-tight">{e(d["name"])}の鍼灸</h1>
      <p class="text-slate-200 text-sm sm:text-[15px] mt-3 leading-relaxed">{e(d["summary"])}</p>
      <div class="grid grid-cols-2 sm:grid-cols-5 gap-2 mt-6 pt-5 border-t border-white/15">
        <a href="#sec-mechanism" class="flex items-center justify-center gap-1 py-2 px-2.5 rounded-xl bg-white/10 hover:bg-white/20 text-xs font-semibold transition-colors">🔬 作用機序</a>
        <a href="#sec-youtube" class="flex items-center justify-center gap-1 py-2 px-2.5 rounded-xl bg-white/10 hover:bg-white/20 text-xs font-semibold transition-colors">🎥 院長動画</a>
        <a href="#sec-acupoints" class="flex items-center justify-center gap-1 py-2 px-2.5 rounded-xl bg-white/10 hover:bg-white/20 text-xs font-semibold transition-colors">📌 ツボ解説</a>
        <a href="#sec-evidence" class="flex items-center justify-center gap-1 py-2 px-2.5 rounded-xl bg-white/10 hover:bg-white/20 text-xs font-semibold transition-colors">📊 論文根拠</a>
        <a href="#sec-faq" class="col-span-2 sm:col-span-1 flex items-center justify-center gap-1 py-2 px-2.5 rounded-xl bg-white/10 hover:bg-white/20 text-xs font-semibold transition-colors">❓ FAQ</a>
      </div>
    </header>

    {content_cards}
    {faq_card_html}

    <div class="flex flex-wrap gap-2">{tags_html}</div>
    {related_html}

    <section class="rounded-3xl bg-[#faf5eb] border border-[#e5d5b5] p-6 sm:p-7 text-center space-y-3">
      <p class="font-mincho text-lg font-bold text-[#16284c]">おひとりで悩まず、まずはご相談ください</p>
      <p class="text-xs sm:text-sm text-slate-600">銀座本院・川口・大宮・横浜・博多・田無の各院でご予約を承っております。</p>
      <div class="flex flex-wrap justify-center gap-3 pt-2">
        <a href="{e(PORTAL_URL)}" class="px-6 py-3 rounded-full bg-[#16284c] hover:bg-[#243b6b] text-white text-sm font-semibold transition-colors">ご予約へ進む（全国各院ポータル）→</a>
        <a href="./index.html" class="px-5 py-3 rounded-full bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 text-sm font-medium transition-colors">← 疾患一覧に戻る</a>
      </div>
    </section>
  </div>

  <div class="sm:hidden fixed bottom-0 inset-x-0 z-40 bg-white/95 backdrop-blur border-t border-slate-200 shadow-[0_-4px_16px_rgba(15,23,42,0.08)] px-3 py-2.5">
    <div class="max-w-lg mx-auto grid grid-cols-12 gap-2 items-center">
      <a href="{e(PORTAL_URL)}" class="col-span-6 py-2.5 px-3 rounded-full bg-[#16284c] text-white text-xs font-bold text-center shadow-sm">📅 空き確認・ご予約</a>
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
  <style>body {{ font-family: 'Noto Sans JP', sans-serif; background-color: #f6f4ef; color: #1e293b; }} .font-mincho {{ font-family: 'Noto Serif JP', serif; }}</style>
</head>
<body class="min-h-screen antialiased">
  <div class="max-w-4xl mx-auto px-4 py-8 sm:py-10">
    <header class="text-center mb-7">
      <h1 class="font-mincho text-2xl sm:text-3xl font-bold text-[#16284c]">そうぜん鍼灸院グループ</h1>
      <p class="text-xs sm:text-sm text-slate-500 mt-1.5">症状・疾患から探す 解説ポータル</p>
      <div class="inline-flex items-center gap-2 bg-white p-1.5 rounded-full border border-[#e8e3d9] shadow-sm mt-5">
        <a href="{e(PORTAL_URL)}" class="px-3.5 sm:px-5 py-2 rounded-full text-xs sm:text-sm font-medium text-slate-600 hover:text-[#16284c]">🏥 鍼灸院から探す</a>
        <span class="px-3.5 sm:px-5 py-2 rounded-full text-xs sm:text-sm font-medium bg-[#16284c] text-white">📝 症状から探す</span>
        <a href="../symptom-check.html" class="px-3.5 sm:px-4 py-2 rounded-full text-xs sm:text-sm font-semibold text-[#8c6239] hover:bg-[#faf5eb]">🧭 30秒ナビ</a>
      </div>
    </header>
    <section class="bg-white rounded-3xl border border-[#e8e3d9] shadow-sm p-5 sm:p-6 space-y-4 mb-6">
      <div class="flex items-center justify-between gap-2">
        <div>
          <p class="text-[11px] font-semibold tracking-widest text-[#b68d40] uppercase">SYMPTOM &amp; DISEASE NAVIGATOR</p>
          <h2 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] mt-0.5">🌿 お悩み・疾患名から探す</h2>
        </div>
        <div class="text-xs text-slate-500">該当：<span id="count" class="font-bold text-[#16284c] text-sm">0</span> 件</div>
      </div>
      <input id="q" type="search" placeholder="疾患名・気になる症状を入力（例：ずなり、めまい、しびれ、自律神経）" class="w-full px-4 py-3 text-sm bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:outline-none focus:ring-2 focus:ring-[#16284c]" />
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
      const thumb = `<img src="./cards/${{esc(d.slug)}}.svg" alt="${{esc(d.name)}}" loading="lazy" class="w-full h-36 sm:h-full object-cover rounded-xl border border-slate-100 shadow-xs" />`;
      const badges = [
        `<span class="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-[#16284c] text-white">${{esc(d.category)}}</span>`,
        d.has_youtube ? `<span class="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-[#eef7fc] text-[#0369a1] border border-[#bae6fd]">🎥 YouTube解説あり</span>` : "",
        d.has_rich ? `<span class="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-[#faf5eb] text-[#8c6239] border border-[#e5d5b5]">★ 詳細解説</span>` : ""
      ].filter(Boolean).join(" ");
      return `<a href="./${{esc(d.slug)}}.html" class="group bg-white rounded-2xl border border-[#e8e3d9] shadow-sm hover:border-[#b68d40] transition-all overflow-hidden flex"><div class="w-1.5 shrink-0 bg-[#16284c] group-hover:bg-[#b68d40]"></div><div class="flex-1 p-4 sm:p-5 grid grid-cols-1 sm:grid-cols-12 gap-4 items-center"><div class="sm:col-span-4">${{thumb}}</div><div class="sm:col-span-8 space-y-2"><div class="flex flex-wrap items-center gap-1.5">${{badges}}</div><h3 class="font-mincho text-lg sm:text-xl font-bold text-[#16284c] group-hover:text-[#b68d40]">${{esc(d.name)}}</h3><p class="text-xs sm:text-sm text-slate-600 leading-relaxed">${{esc(d.summary)}}</p><div class="text-right text-xs font-semibold text-[#16284c]">詳しい解説を読む →</div></div></div></a>`;
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
      document.getElementById("list").innerHTML = slice.map(cardHtml).join("") || '<p class="text-center text-sm text-slate-500 py-10 bg-white rounded-2xl border border-[#e8e3d9]">該当する疾患が見つかりませんでした。別のひらがな・キーワードでお試しください。</p>';

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
    cards_dir = DISEASE_DIR / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    mech_dir = DISEASE_DIR / "mechanisms"
    mech_dir.mkdir(parents=True, exist_ok=True)
    for d in diseases:
        (cards_dir / f"{d['slug']}.svg").write_text(generate_unique_card_svg(d), encoding="utf-8")
        (mech_dir / f"{d['slug']}.svg").write_text(generate_mechanism_svg(d), encoding="utf-8")
        (DISEASE_DIR / f"{d['slug']}.html").write_text(render_detail_html(d, by_category), encoding="utf-8")
        if GENERATE_STATIC_REDIRECTS and d["legacy_path"]:
            write_static_redirect_page(d["legacy_path"], d["slug"])

    (DISEASE_DIR / "index.html").write_text(render_index_html(diseases), encoding="utf-8")
    (DISEASE_DIR / "diseases.json").write_text(json.dumps(diseases, ensure_ascii=False, indent=2), encoding="utf-8")
    (DISEASE_DIR / "legacy_redirects.json").write_text(json.dumps(legacy_map, ensure_ascii=False, indent=2), encoding="utf-8")
    (BASE_DIR / "sitemap.xml").write_text(render_sitemap(diseases), encoding="utf-8")
    print(f"OK: {len(diseases)} 件の疾患ページ（新カードデザイン・公式YouTube連動・1,085枚専用カード）を生成しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
