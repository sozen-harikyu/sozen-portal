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
            # 旧サイト(s-treatment.com)由来のフリー素材画像はすべて除外し、当院独自の図解画像のみ許可
            fname = img_match.group(1).strip()
            caption = (img_match.group(2) or "").strip()
            if (IMAGES_DIR / fname).exists() and not fname.startswith(("http://", "https://")):
                src = f"../images/diseases/{fname}"
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



# 筋肉、骨のお悩み：部位別ツボ図解（5部位自動出し分け）
MUSCLE_REGION_ACUPOINTS = {
    "neck_shoulder": {
        "heading": "首・肩・上腕のツボ図解とセルフケア",
        "domain_label": "首・肩・上腕（肩関節・頸椎・肩甲帯）の筋骨格系トラブル",
        "diagram": "muscle_neck_shoulder_acupoints.jpg",
        "diagram_caption": "図：首・肩・上腕の痛み・可動域制限に用いられる代表的なツボ（風池・肩井・天宗・肩髃・臂臑・曲池）の位置目安。",
        "acupoints": [
            ("肩髃（けんぐう）", "腕を横に挙げたときに肩の前の角にできるくぼみ", "上腕二頭筋長頭腱炎・五十肩・肩関節周囲の痛みと可動域改善", "炎症や夜間痛が強い時期は強く揉み込まず、温めや軽い圧迫に留めます。"),
            ("臂臑（ひじゅ）", "肩の三角筋の先端（腕の外側の境目のくぼみ）", "上腕の重だるさ・腕を上げる際の引っかかり感・肩関節の負担軽減", "骨に向かって垂直に3〜5秒じんわり押す程度が安全です。"),
            ("天宗（てんそう）", "肩甲骨のほぼ中央、押すと響くくぼみ", "肩甲骨周囲の深層筋（棘下筋）の緊張緩和・腕への放散痛ケア", "テニスボール等で強く押しすぎず、心地よい響きで止めます。"),
            ("肩井（けんせい）", "首の付け根と肩先を結んだ線の中央", "僧帽筋の過緊張緩和・首肩のこりや張り改善", "妊娠中の方は強い刺激を避け、優しく温める程度にしてください。"),
            ("風池（ふうち）", "後頭部の生え際、耳の後ろの骨と首筋の間のくぼみ", "頸椎周囲の深層筋緊張・首こり・肩腕への神経負担緩和", "親指で頭の中心に向かって優しく支えるように押します。"),
            ("曲池（きょくち）", "肘を曲げたときにできる外側のシワの先端", "上腕〜前腕の筋膜連動の調整・肩肘の炎症・だるさ軽減", "反対側の親指でゆっくり5秒押して離すリズムで行います。"),
        ],
    },
    "elbow_wrist": {
        "heading": "肘・前腕・手首・指のツボ図解とセルフケア",
        "domain_label": "肘・前腕・手首・指（腱鞘炎・テニス肘・手根管等）の筋骨格系トラブル",
        "diagram": "muscle_elbow_wrist_acupoints.jpg",
        "diagram_caption": "図：肘・前腕・手首・指の痛みやしびれに用いられる代表的なツボ（曲池・手三里・外関・合谷・内関・大陵）の位置目安。",
        "acupoints": [
            ("曲池（きょくち）", "肘を曲げたときにできる外側のシワの先端", "外側上顆炎（テニス肘）・前腕伸筋群の過緊張緩和", "強い腫れや熱感がある急性期は直接強く揉まないようにします。"),
            ("手三里（てさんり）", "曲池から手首方向へ指3本分下がった筋肉の盛り上がり", "前腕の張り・腱鞘炎やバネ指につながる筋疲労の緩和", "痛気持ちいい強さで3〜5秒ずつゆっくり圧迫します。"),
            ("外関（がいかん）", "手の甲側、手首のシワから指3本分肘寄りの2本の骨の間", "手首の背屈痛・前腕のねじり動作痛・自律神経の調整", "手首を軽く緩めた状態で優しく押します。"),
            ("合谷（ごうこく）", "手の甲側、親指と人差し指の骨が交わる手前のくぼみ", "手指の痛み・疼痛抑制系（下行性抑制）の活性化", "妊娠中の方は強い刺激を控えてください。"),
            ("内関（ないかん）", "手のひら側、手首のシワから指3本分肘寄りの2本の腱の間", "手根管周囲の緊張緩和・前腕屈筋群と自律神経の調整", "しびれが強く響く場合は圧を弱めて軽くさすります。"),
            ("大陵（だいりょう）", "手のひら側、手首の横ジワのほぼ中央", "手首の痛み・手根管症候群や腱鞘の過負担ケア", "神経が浅い場所のため、ゴリゴリ揉まず優しく触れる・温める程度にします。"),
        ],
    },
    "hip_knee": {
        "heading": "股関節・膝・太もものツボ図解とセルフケア",
        "domain_label": "股関節・膝・太もも（変形性膝関節症・ランナー膝等）の筋骨格系トラブル",
        "diagram": "muscle_hip_knee_acupoints.jpg",
        "diagram_caption": "図：股関節・膝・太ももの痛み・こわばりに用いられる代表的なツボ（梁丘・血海・陰陵泉・犢鼻・足三里・陽陵泉）の位置目安。",
        "acupoints": [
            ("梁丘（りょうきゅう）", "膝のお皿の外上角から指3本分上の太もものくぼみ", "大腿四頭筋の緊張緩和・階段昇降や立ち上がり時の膝痛ケア", "膝を軽く伸ばして力を抜いた状態で優しく押します。"),
            ("血海（けっかい）", "膝のお皿の内上角から指3本分上の筋肉のくぼみ", "膝内側の痛み・内側広筋の血流改善とむくみケア", "強く押しすぎず、手のひらで温めながらじんわり圧迫します。"),
            ("犢鼻（とくび）", "膝を軽く曲げたとき、お皿の下の外側にできるくぼみ", "膝関節前面の痛み・関節周囲の循環サポート", "関節に水が溜まっている・熱感がある時期は直接押さず温めも控えます。"),
            ("足三里（あしさんり）", "膝のお皿の下外側（犢鼻）から指4本分下のすねの外側", "下肢全体の筋疲労回復・膝周囲の筋バランスと鎮痛サポート", "セルフ灸や指圧の定番穴として毎日続けやすいツボです。"),
            ("陽陵泉（ようりょうせん）", "膝の外側下にある丸い骨（腓骨頭）の斜め下のくぼみ", "腸脛靭帯炎（ランナー膝）・筋腱の柔軟性回復", "しびれが出やすい神経の近くのため、優しい圧で調整します。"),
            ("陰陵泉（いんりょうせん）", "すねの内側の骨を膝方向へなぞり、指が止まるくぼみ", "鵞足炎・膝内側の張り・関節周囲のむくみ緩和", "骨の際を無理にグリグリ押さず、気持ちよい範囲で押します。"),
        ],
    },
    "ankle_foot": {
        "heading": "ふくらはぎ・足首・足裏のツボ図解とセルフケア",
        "domain_label": "ふくらはぎ・足首・足裏（アキレス腱炎・足底筋膜炎・捻挫等）の筋骨格系トラブル",
        "diagram": "muscle_ankle_foot_acupoints.jpg",
        "diagram_caption": "図：ふくらはぎ・足首・足裏の痛み・こわばりに用いられる代表的なツボ（承山・崑崙・三陰交・太渓・照海・湧泉）の位置目安。",
        "acupoints": [
            ("承山（しょうざん）", "ふくらはぎの中央、アキレス腱から筋肉へ切り替わるくぼみ", "こむら返り・アキレス腱や足底筋膜への牽引負担軽減", "立位で強く押さず、座ってふくらはぎを緩めた状態で押します。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上、すねの骨の後ろ際", "シンスプリント・下腿内側の循環改善と冷え・むくみケア", "妊娠中の方は強い刺激を控え、優しく温める程度にします。"),
            ("太渓（たいけい）", "内くるぶしとアキレス腱の間にあるくぼみ", "アキレス腱周囲炎・足首の柔軟性回復と局所血流サポート", "アキレス腱自体を強くつままず、くぼみに指を添えて優しく押します。"),
            ("崑崙（こんろん）", "外くるぶしとアキレス腱の間にあるくぼみ", "足関節捻挫後のこわばり・踵やふくらはぎ外側の痛みケア", "腫れや内出血がある捻挫直後は安静・冷却を優先してください。"),
            ("照海（しょうかい）", "内くるぶしの真下にある小さなくぼみ", "足首内側の安定化・扁平足や足底の疲労回復", "足首を軽く回しながら優しく刺激すると緩みやすくなります。"),
            ("湧泉（ゆうせん）", "足裏の中央よりやや前、指を曲げたときにできるくぼみ", "足底筋膜炎の緊張緩和・足裏の疲労と冷え改善", "足裏に強い炎症・激痛がある時期は患部圧迫を避け、ふくらはぎ側からケアします。"),
        ],
    },
}


def resolve_muscle_acupoint_profile(d: dict, base_ev: dict) -> dict:
    """筋肉、骨のお悩みについて、疾患名・キーワードから最適な部位別ツボ図解プロファイルを返す。"""
    if d.get("category") != "筋肉、骨のお悩み":
        return base_ev
    text = f"{d.get('name', '')} {d.get('keywords', '')} {d.get('slug', '')}"
    if any(k in text for k in ["肘", "前腕", "手首", "手関節", "腱鞘", "ばね指", "バネ指", "ドケルバン", "手根管", "テニス肘", "ゴルフ肘", "外側上顆", "内側上顆", "尺骨", "橈骨", "母指", "CM関節", "ヘバーデン", "ブシャール", "肘部管", "ギヨン", "突き指", "手指"]):
        region = MUSCLE_REGION_ACUPOINTS["elbow_wrist"]
    elif any(k in text for k in ["上腕", "肩", "首", "頸", "頚", "鎖骨", "斜角筋", "胸郭出口", "寝違え", "むちうち", "背中", "肩甲", "ストレートネック", "バーナー", "腱板", "五十肩", "四十肩", "肋間", "側弯", "猫背"]):
        region = MUSCLE_REGION_ACUPOINTS["neck_shoulder"]
    elif any(k in text for k in ["足首", "足関節", "アキレス", "足底", "踵", "かかと", "捻挫", "シンスプリント", "ふくらはぎ", "下腿", "こむら", "外反母趾", "内反小趾", "モートン", "扁平足", "中足骨", "腓骨", "足根管", "セーバー", "シーバー", "外脛骨"]):
        region = MUSCLE_REGION_ACUPOINTS["ankle_foot"]
    elif any(k in text for k in ["膝", "ひざ", "股関節", "大腿", "太もも", "オスグッド", "半月板", "鵞足", "腸脛", "ランナー膝", "ジャンパー膝", "鼠径", "グロイン", "関節水腫", "ベーカー", "タナ障害", "ハンター管", "大腿四頭筋", "ハムストリング"]):
        region = MUSCLE_REGION_ACUPOINTS["hip_knee"]
    else:
        return base_ev
    merged = dict(base_ev)
    merged.update(region)
    return merged



# 全1,200疾患対応：難病・がん・眼・鼻・喉・耳・全身アプローチ専門ツボ図解（32種＋筋骨格4種＋既存20種＝計56種体制）
SPECIALIZED_ACUPOINT_PROFILES = {
    "rare_parkinson": {
        "heading": "パーキンソン病・振戦の全身調整ツボ図解",
        "domain_label": "パーキンソン病・本態性振戦などの運動制御・自律神経サポート",
        "diagram": "rare_parkinson_acupoints.jpg",
        "diagram_caption": "図：パーキンソン病・振戦の全身調整に用いられる代表的なツボ（百会・風池・曲池・合谷・陽陵泉・太衝）の位置目安。",
        "acupoints": [
            ("百会（ひゃくえ）", "頭頂部のほぼ中央、両耳を結んだ線上", "中枢の過覚醒鎮静・睡眠の質と姿勢バランスのサポート", "指の腹で軽く押さえる程度に留めます。"),
            ("風池（ふうち）", "後頭部の生え際、首筋の外側のくぼみ", "頸部深層筋のこわばり緩和・脳循環サポート", "強く押し込まず頭を支えるように優しく刺激します。"),
            ("曲池（きょくち）", "肘を曲げた外側のシワの先端", "上肢の筋固縮・震えに伴う腕の張り緩和", "ゆっくり呼吸しながら3〜5秒圧迫します。"),
            ("合谷（ごうこく）", "手の甲側、親指と人差し指の骨の間", "自律神経の鎮静・手指のこわばり軽減", "無理のない優しい圧で行います。"),
            ("陽陵泉（ようりょうせん）", "膝の外側下にある丸い骨の斜め下のくぼみ", "「筋会」として下肢の筋固縮・歩き出しの補助", "座った姿勢でリラックスして押します。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨が交わる手前", "不随意運動に伴う交感神経過緊張・不安感の緩和", "就寝前の優しい指圧や温めがおすすめです。"),
        ],
    },
    "rare_cerebellar_balance": {
        "heading": "平衡障害・ふらつき・小脳系トラブルの全身ツボ図解",
        "domain_label": "脊髄小脳変性症・歩行平衡障害などのバランス・下肢支持サポート",
        "diagram": "rare_cerebellar_balance_acupoints.jpg",
        "diagram_caption": "図：ふらつき・平衡障害の全身調整に用いられる代表的なツボ（百会・完骨・風池・足三里・懸鐘・太衝）の位置目安。",
        "acupoints": [
            ("百会（ひゃくえ）", "頭頂部の中央", "体幹の軸感覚・自律神経の安定化", "座位で転倒に注意しながら優しく触れます。"),
            ("完骨（かんこつ）", "耳の後ろにある出っ張った骨（乳様突起）の下後ろ", "平衡感覚・後頭部深層筋の緊張緩和", "親指で優しく支える程度に押します。"),
            ("風池（ふうち）", "後頭部生え際のくぼみ", "椎骨脳底動脈周囲の筋緊張緩和・ふらつきケア", "首を大きく反らさず穏やかに刺激します。"),
            ("足三里（あしさんり）", "膝のお皿の下外側から指4本分下", "下肢の筋疲労回復・歩行リハビリの土台づくり", "温灸や穏やかな指圧で下肢の循環を促します。"),
            ("懸鐘（けんしょう）", "外くるぶしの上から指4本分上", "足首の安定性・下肢の重だるさ軽減", "無理な立位を避け座位でケアします。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨の間", "足底接地感の意識づけ・自律神経の鎮静", "お風呂上がりなどに優しくさすります。"),
        ],
    },
    "rare_stroke_swallow": {
        "heading": "脳卒中後遺症・嚥下・構音リハビリ併用のツボ図解",
        "domain_label": "脳卒中後の嚥下障害・構音障害・片麻痺リハビリ併用ケア",
        "diagram": "rare_stroke_swallow_acupoints.jpg",
        "diagram_caption": "図：脳卒中後遺症・嚥下・構音リハビリ併用に用いられる代表的なツボ（百会・風池・廉泉・内関・合谷・足三里）の位置目安。",
        "acupoints": [
            ("百会（ひゃくえ）", "頭頂部の中央", "覚醒度・神経可塑性・気分の安定サポート", "穏やかなタッチで頭部の緊張を緩めます。"),
            ("風池（ふうち）", "後頭部の生え際のくぼみ", "項部筋の過緊張緩和・嚥下反射の補助", "無理のない座位・側臥位で優しく刺激します。"),
            ("廉泉（れんせん）", "あごの下、のど仏の上のくぼみ", "舌骨上筋群・咽頭周囲の協調運動サポート", "セルフでは強く押さず、専門家の施術に委ねます。"),
            ("内関（ないかん）", "手首の横ジワから指3本分肘寄りの中央", "自律神経の落ち着き・リハビリ時の不安軽減", "健側・患側とも優しい圧で行います。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "上肢の循環促進・口腔顔面領域の遠隔調整", "抗凝固薬服用中は内出血に注意し優しく押します。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "下肢の歩行リハビリ補助・胃腸機能サポート", "温めや軽いさすり刺激で循環を助けます。"),
        ],
    },
    "rare_ms_mg_systemic": {
        "heading": "神経免疫難病・全身倦怠感の土台づくりツボ図解",
        "domain_label": "多発性硬化症・重症筋無力症・膠原病等の疲労・自律神経サポート",
        "diagram": "rare_ms_mg_systemic_acupoints.jpg",
        "diagram_caption": "図：神経免疫難病の全身疲労・自律神経ケアに用いられる代表的なツボ（百会・気海・関元・足三里・三陰交・太渓）の位置目安。",
        "acupoints": [
            ("百会（ひゃくえ）", "頭頂部の中央", "中枢疲労の緩和・自律神経バランス調整", "疲労が強い日は極低刺激に留めます。"),
            ("気海（きかい）", "おへそから指2本分下", "全身の回復力・呼吸の深さのサポート", "手のひらで温める（手当て）だけでも有効です。"),
            ("関元（かんげん）", "おへそから指4本分下", "深部体温・下腹部の巡りと体力維持", "熱いお灸は避け心地よい温かさに留めます。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "胃腸吸収と全身の体力・倦怠感ケア", "刺激過多にならないよう短時間で行います。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "末梢循環・睡眠の質・ホルモン自律神経の調和", "妊娠中は強い刺激を避けます。"),
            ("太渓（たいけい）", "内くるぶしとアキレス腱の間のくぼみ", "深い疲労感・冷え・回復力の底上げ", "優しく温めながら深呼吸を合わせます。"),
        ],
    },
    "rare_rheumatoid_immune": {
        "heading": "関節リウマチ・膠原病の全身免疫・関節ケアツボ図解",
        "domain_label": "関節リウマチ・膠原病・自己免疫疾患の標準治療併用サポート",
        "diagram": "rare_rheumatoid_immune_acupoints.jpg",
        "diagram_caption": "図：関節リウマチ・膠原病の全身調整に用いられる代表的なツボ（曲池・外関・合谷・足三里・陰陵泉・太渓）の位置目安。",
        "acupoints": [
            ("曲池（きょくち）", "肘を曲げた外側のシワの先端", "全身の炎症バランス・上肢のこわばり緩和", "腫れや熱感が強い関節自体は避け周囲から調整します。"),
            ("外関（がいかん）", "手の甲側、手首から指3本分肘寄り", "手首・手指のこわばりや朝の動かしにくさ補助", "無理に関節を動かさず穏やかに刺激します。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "下行性疼痛抑制系の活性化・痛みの波の緩和", "ステロイド・免疫抑制薬使用中は皮膚保護を最優先します。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "胃腸保護・全身の免疫自律神経バランス調整", "体力の消耗を防ぐ穏やかな刺激にします。"),
            ("陰陵泉（いんりょうせん）", "すねの内側、膝下のくぼみ", "関節のむくみ・重だるさ・水分代謝サポート", "骨の際を優しくさする程度にします。"),
            ("太渓（たいけい）", "内くるぶしとアキレス腱の間", "冷え・慢性疲労・下肢関節の循環サポート", "温めと組み合わせるとこわばりが緩みやすくなります。"),
        ],
    },
    "rare_ibd_gut_immune": {
        "heading": "潰瘍性大腸炎・クローン病（IBD）の腸管自律神経ツボ図解",
        "domain_label": "炎症性腸疾患（潰瘍性大腸炎・クローン病）・過敏性腸症候群の併用ケア",
        "diagram": "rare_ibd_gut_immune_acupoints.jpg",
        "diagram_caption": "図：腸管の自律神経と粘膜回復を支える代表的なツボ（中脘・天枢・関元・足三里・上巨虚・三陰交）の位置目安。",
        "acupoints": [
            ("中脘（ちゅうかん）", "みぞおちとおへその中間", "胃腸の蠕動バランス・食後の張り軽減", "腹痛が強い活動期は押さず温めるだけにします。"),
            ("天枢（てんすう）", "おへその左右それぞれ指3本分外側", "大腸の運動調整・下痢や腹鳴の緩和", "腹部に強い圧痛がある場合は遠隔穴を優先します。"),
            ("関元（かんげん）", "おへそから指4本分下", "下腹部の冷え改善・迷走神経を介した腸管鎮静", "穏やかな温灸が特に相性の良いツボです。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "迷走神経抗炎症反射の活性化・栄養吸収サポート", "日常のセルフケアに最も安全な遠隔穴です。"),
            ("上巨虚（じょうこきょ）", "足三里からさらに指4本分下", "大腸の「下合穴」として腸管機能と排便リズム調整", "すねの外側を優しく押してほぐします。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "自律神経・睡眠・ストレス性の腸過敏緩和", "下痢が続く時期の冷え対策にも役立ちます。"),
        ],
    },
    "rare_fibromyalgia_cfs": {
        "heading": "線維筋痛症・慢性疲労症候群の全身鎮痛ツボ図解",
        "domain_label": "線維筋痛症・筋痛性脳脊髄炎/慢性疲労症候群（ME/CFS）の中枢鎮静ケア",
        "diagram": "rare_fibromyalgia_cfs_acupoints.jpg",
        "diagram_caption": "図：全身の広範な痛み・倦怠感の鎮静に用いられる代表的なツボ（百会・印堂・内関・合谷・足三里・太衝）の位置目安。",
        "acupoints": [
            ("百会（ひゃくえ）", "頭頂部の中央", "中枢性感作（脳の痛み過敏）と睡眠障害の鎮静", "触れるだけの極低刺激から始めます。"),
            ("印堂（いんどう）", "左右の眉間の真ん中", "自律神経の過覚醒・ブレインフォグ・不安感の緩和", "目を閉じてゆっくり呼吸しながら軽く触れます。"),
            ("内関（ないかん）", "手首の横ジワから指3本分肘寄りの中央", "交感神経の高ぶり・動悸・吐き気の鎮静", "労作後倦怠感（PEM）を起こさない穏やかな刺激にします。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "全身の疼痛閾値の安定化・頭頸部の緊張緩和", "強い圧痛が出ない範囲で優しく押します。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "全身の回復力・消化吸収・自律神経の土台づくり", "体調の波に合わせて本数と時間を調整します。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨の間", "筋の過緊張とストレス反応の鎮静", "痛みが強い部位を避け遠隔から整えます。"),
        ],
    },
    "rare_crps_neuropathy": {
        "heading": "CRPS・末梢神経障害の遠隔・健側アプローチツボ図解",
        "domain_label": "複合性局所疼痛症候群（CRPS）・神経障害性疼痛の安全な遠隔ケア",
        "diagram": "rare_crps_neuropathy_acupoints.jpg",
        "diagram_caption": "図：過敏な患部を避けた遠隔・健側アプローチに用いられる代表的なツボ（外関・内関・合谷・陽陵泉・三陰交・太衝）の位置目安。",
        "acupoints": [
            ("外関（がいかん）", "手の甲側、手首から指3本分肘寄り", "上肢の神経過敏・放散痛の遠隔調整", "アロディニア（接触痛）がある患側は避け健側を使います。"),
            ("内関（ないかん）", "手のひら側、手首から指3本分肘寄り", "交感神経依存性の痛み・不安感の鎮静", "強く押し込まず軽く添える程度にします。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "中枢性の下行性疼痛抑制系の活性化", "健側への穏やかな刺激で全体の痛みを和らげます。"),
            ("陽陵泉（ようりょうせん）", "膝の外側下、丸い骨の斜め下のくぼみ", "下肢の神経周囲の筋緊張・しびれ感の緩和", "電撃痛が出ない優しい圧に留めます。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "末梢の微小循環改善・夜間痛による不眠ケア", "温めと併用すると末梢血流が整いやすくなります。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨の間", "自律神経の過緊張・足先のピリピリ感調整", "皮膚感覚が過敏な場合は触れずに遠隔穴を選びます。"),
        ],
    },
    "rare_meniere_vertigo": {
        "heading": "メニエール病・難治性めまい・耳鳴りのツボ図解",
        "domain_label": "メニエール病・前庭性片頭痛・耳鳴り・難治性めまいの内耳循環ケア",
        "diagram": "rare_meniere_vertigo_acupoints.jpg",
        "diagram_caption": "図：内耳循環と前庭自律神経を整える代表的なツボ（聴宮・翳風・風池・完骨・内関・太衝）の位置目安。",
        "acupoints": [
            ("聴宮（ちょうきゅう）", "耳の穴の前、口を開けるとくぼむ場所", "内耳周囲の微小循環改善・耳閉感や耳鳴りケア", "強く押さず口を少し開けて優しく触れます。"),
            ("翳風（えいふう）", "耳たぶのすぐ後ろのくぼみ", "内耳・三叉神経・顔面周囲の血流とリンパ循環促進", "めまい発作直後は首を動かさず静かに温めます。"),
            ("風池（ふうち）", "後頭部の生え際のくぼみ", "後頭下筋群の緊張緩和・椎骨動脈血流サポート", "親指で上向きに優しく支えるように押します。"),
            ("完骨（かんこつ）", "耳の後ろの出っ張った骨の下後ろ", "平衡感覚の安定・睡眠の質改善", "左右の張りを比べながら優しく緩めます。"),
            ("内関（ないかん）", "手首の横ジワから指3本分肘寄りの中央", "めまいに伴う吐き気・自律神経発作の鎮静", "めまいが出そうなときのセルフ指圧にも有効です。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨の間", "ストレス性の内リンパ水腫悪化・イライラの緩和", "深呼吸と合わせて3〜5秒ずつ押します。"),
        ],
    },
    "rare_facial_trigeminal": {
        "heading": "顔面神経麻痺・三叉神経痛の顔面・遠隔ツボ図解",
        "domain_label": "顔面神経麻痺（ベル麻痺等）・三叉神経痛の神経回復と鎮痛ケア",
        "diagram": "rare_facial_trigeminal_acupoints.jpg",
        "diagram_caption": "図：顔面神経・三叉神経領域の調整に用いられる代表的なツボ（陽白・四白・地倉・翳風・頬車・合谷）の位置目安。",
        "acupoints": [
            ("陽白（ようはく）", "眉毛の中央から指1本分上の額", "額のしわ寄せ・まぶたの動き・第1枝領域の調整", "三叉神経痛のトリガー部位は直接刺激せず遠隔穴を優先します。"),
            ("四白（しはく）", "黒目の真下、頬骨の小さなくぼみ", "頬周囲の微小循環・第2枝領域の過敏鎮静", "顔面は皮膚が薄いため極めて優しいタッチで行います。"),
            ("地倉（ちそう）", "口角のすぐ外側", "口元の動き・食事時のこぼれにくさサポート", "強いマッサージは病的共同運動を招くため避けます。"),
            ("頬車（きょうしゃ）", "えらの角から少し前上、噛むと盛り上がる場所", "咬筋の過緊張緩和・下顎周囲の血流改善", "蒸しタオルで温めながら軽く緩めると安全です。"),
            ("翳風（えいふう）", "耳たぶの後ろのくぼみ（顔面神経出口付近）", "顔面神経周囲の浮腫軽減・循環サポート", "発症急性期は必ず耳鼻科・神経内科の標準治療を最優先します。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "「面口合谷収」と呼ばれる顔面・口腔の最重要遠隔穴", "顔に触れると痛い三叉神経痛でも安全に使える遠隔穴です。"),
        ],
    },
    "cancer_pain_palliative": {
        "heading": "がん性疼痛・緩和ケアの全身アプローチツボ図解",
        "domain_label": "がん性疼痛・緩和ケアにおける標準治療併用の全身サポート",
        "diagram": "cancer_pain_palliative_acupoints.jpg",
        "diagram_caption": "図：がん性疼痛・緩和ケアの全身調整に用いられる代表的なツボ（百会・内関・合谷・足三里・三陰交・太衝）の位置目安。",
        "acupoints": [
            ("百会（ひゃくえ）", "頭頂部の中央", "痛みへの不安・気分の落ち込み・睡眠の質サポート", "体力を消耗しない極低刺激で穏やかに整えます。"),
            ("内関（ないかん）", "手首の横ジワから指3本分肘寄りの中央", "吐き気・胸の苦しさ・オピオイドに伴う不快感軽減", "点滴や採血部位を避けて安全に刺激します。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "内因性鎮痛系（エンドルフィン等）の活性化", "腫瘍部位や転移部位への直接刺鍼は行いません。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "胃腸機能・オピオイド便秘予防・体力維持", "好中球・血小板低下時は刺さない温熱や指圧に切り替えます。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "下半身の冷え・睡眠・全身の自律神経バランス", "浮腫が強い部位は避け皮膚を傷つけないよう配慮します。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨の間", "痛みに伴う緊張・怒りや焦燥感の鎮静", "主治医・緩和ケアチームの鎮痛薬治療と併用します。"),
        ],
    },
    "cancer_cipn_numbness": {
        "heading": "抗がん剤末梢神経障害（CIPN・手足のしびれ）ツボ図解",
        "domain_label": "化学療法誘発性末梢神経障害（CIPN）の手足しびれ・感覚異常ケア",
        "diagram": "cancer_cipn_numbness_acupoints.jpg",
        "diagram_caption": "図：抗がん剤による手足のしびれ（CIPN）に用いられる代表的なツボ（外関・合谷・八邪・足三里・太衝・八風）の位置目安。",
        "acupoints": [
            ("外関（がいかん）", "手の甲側、手首から指3本分肘寄り", "前腕〜手指にかけての微小循環と神経伝導サポート", "リンパ節郭清側の腕には過度な刺激を避けます。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "手指の感覚回復・ピリピリ感の鎮静", "抗がん剤投与サイクルと血球値を確認して行います。"),
            ("八邪（はちじゃ）", "手指の付け根の水かき部分", "指先の細かなしびれ・ボタン掛けのしづらさケア", "清潔な手で優しく揉みほぐすセルフケアも有効です。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "下肢全体の血流促進・歩行のふらつき予防", "冷えやすい下肢を温めながら調整します。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨の間", "足背〜足趾の血流改善・神経痛の波の軽減", "感覚鈍麻がある場合は火傷防止のため強い温熱を避けます。"),
            ("八風（はっぷう）", "足指の付け根の水かき部分", "足裏・足先のしびれ感・転倒予防サポート", "血小板減少時は刺鍼せず優しい指圧に留めます。"),
        ],
    },
    "cancer_cinv_anorexia": {
        "heading": "抗がん剤悪心嘔吐・食欲不振（CINV・CIA）ツボ図解",
        "domain_label": "化学療法に伴う吐き気・食欲不振・栄養サポート",
        "diagram": "cancer_cinv_anorexia_acupoints.jpg",
        "diagram_caption": "図：抗がん剤治療中の吐き気・食欲不振に用いられる代表的なツボ（中脘・天枢・気海・内関・足三里・公孫）の位置目安。",
        "acupoints": [
            ("内関（ないかん）", "手首の横ジワから指3本分肘寄りの中央", "CINV（化学療法誘発性悪心嘔吐）の最重要国際根拠穴", "吐き気を感じた際のセルフ指圧やリストバンドも有効です。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "胃排出能の促進・食欲不振（CIA）と体力維持", "標準的な制吐薬と併用して胃腸の動きを支えます。"),
            ("中脘（ちゅうかん）", "みぞおちとおへその中間", "胃のむかつき・食後の重だるさ軽減", "腹部手術痕や腹水がある場合は遠隔穴のみを使います。"),
            ("天枢（てんすう）", "おへその左右それぞれ指3本分外側", "腸管運動の調整・便秘や下痢のバランス改善", "強い圧迫は避け、温かい手のひらで包むケアが安全です。"),
            ("気海（きかい）", "おへそから指2本分下", "体重減少・体力低下時の基礎エネルギー回復補助", "血球減少期は刺さない温灸等で穏やかに行います。"),
            ("公孫（こうそん）", "足の親指の付け根からかかと寄り、土踏まずの縁", "内関と対（八脈交会穴）で働く胃腸・吐き気調整穴", "内関と組み合わせて優しく押すと胃が落ち着きやすくなります。"),
        ],
    },
    "cancer_fatigue_crf": {
        "heading": "がん関連倦怠感（CRF）・体力回復の全身ツボ図解",
        "domain_label": "がん治療中・治療後の強い倦怠感（CRF）と体力回復サポート",
        "diagram": "cancer_fatigue_crf_acupoints.jpg",
        "diagram_caption": "図：がん関連倦怠感（CRF）の体力・自律神経回復に用いられる代表的なツボ（百会・気海・関元・足三里・三陰交・太渓）の位置目安。",
        "acupoints": [
            ("百会（ひゃくえ）", "頭頂部の中央", "頭のだるさ・ブレインフォグ（ケモブレイン）の軽減", "短時間・極低刺激で自律神経を穏やかに整えます。"),
            ("気海（きかい）", "おへそから指2本分下", "呼吸の深さ・気力と体力の底上げ", "放射線照射野に当たる場合は腹部を避けます。"),
            ("関元（かんげん）", "おへそから指4本分下", "冷え・慢性疲労・睡眠リズムの土台づくり", "優しい温熱ケアとの相性が良好です。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "CRF臨床試験で最頻用される体力・免疫サポート穴", "貧血や感染兆候がないか主治医の検査値も確認します。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "睡眠の質改善・下肢のだるさと冷え軽減", "むくみが強い時期は皮膚に負担をかけない手法を選びます。"),
            ("太渓（たいけい）", "内くるぶしとアキレス腱の間", "治療後の深い消耗感・回復力のサポート", "就寝前の優しい温め・指圧もおすすめです。"),
        ],
    },
    "cancer_headneck_xerostomia": {
        "heading": "頭頸部がん放射線後遺症・口腔乾燥・嚥下のツボ図解",
        "domain_label": "頭頸部がん放射線治療後の口腔乾燥症・口腔粘膜炎・頸部郭清術後ケア",
        "diagram": "cancer_headneck_xerostomia_acupoints.jpg",
        "diagram_caption": "図：放射線誘発性口腔乾燥症・嚥下ケアに用いられる代表的なツボ（承漿・廉泉・翳風・合谷・三陰交・太渓）の位置目安。",
        "acupoints": [
            ("承漿（しょうしょう）", "下唇のすぐ下、あごのくぼみ", "唾液腺反射の促進・口周りの乾燥感軽減", "放射線皮膚炎や線維化がある部位への強刺激は避けます。"),
            ("廉泉（れんせん）", "あごの下、のど仏の上のくぼみ", "嚥下反射・舌根部の動きと唾液分泌サポート", "照射野の皮膚状態を必ず確認し遠隔穴を中心に設計します。"),
            ("翳風（えいふう）", "耳たぶの後ろのくぼみ（耳下腺周囲）", "残存唾液腺の副交感神経刺激・頸部の張り緩和", "強い圧迫は避け優しく触れる程度にします。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "JAMA試験等でも用いられる顔面・口腔の最重要遠隔穴", "照射部位から離れた安全なツボとして最も活用されます。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "全身の潤い（津液）バランスと睡眠の質改善", "標準的な口腔保湿ケア・歯科管理と併用します。"),
            ("太渓（たいけい）", "内くるぶしとアキレス腱の間", "慢性の乾燥感・ほてり・治療後の体力回復サポート", "毎日の優しいセルフ指圧にも取り入れやすいツボです。"),
        ],
    },
    "cancer_hormone_hotflash": {
        "heading": "ホルモン療法ホットフラッシュ・関節痛の全身ツボ図解",
        "domain_label": "乳がん・前立腺がんホルモン療法に伴うほてり・関節痛サポート",
        "diagram": "cancer_hormone_hotflash_acupoints.jpg",
        "diagram_caption": "図：ホルモン療法の副作用（ほてり・寝汗・関節痛）に用いられる代表的なツボ（内関・合谷・陰陵泉・三陰交・太渓・太衝）の位置目安。",
        "acupoints": [
            ("内関（ないかん）", "手首の横ジワから指3本分肘寄りの中央", "急なほてり・発汗に伴う動悸と不安感の鎮静", "乳がん術側の腕（リンパ浮腫リスク側）は避けて健側を使います。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "アロマターゼ阻害薬誘発性関節痛（AIA）の鎮痛補助", "手首や指のこわばりにも穏やかに作用します。"),
            ("陰陵泉（いんりょうせん）", "すねの内側、膝下のくぼみ", "関節のむくみ・重だるさ・水分代謝の調整", "ホルモン治療の継続を支える非薬物ケアとして行います。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "体温調節中枢の過敏鎮静・夜間覚醒の軽減", "週1〜2回の継続でほてりの波が穏やかになりやすいツボです。"),
            ("太渓（たいけい）", "内くるぶしとアキレス腱の間", "ほてりと足元の冷え（上熱下寒）のバランス調整", "寝汗やのぼせが強い方におすすめのツボです。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨の間", "自律神経の急上昇（ホットフラッシュ発作）の抑制", "深呼吸しながら3〜5秒ずつ優しく押します。"),
        ],
    },
    "eye_glaucoma_optic": {
        "heading": "緑内障・視神経ケアの眼周囲・全身血流ツボ図解",
        "domain_label": "緑内障・視神経萎縮・眼圧負担の眼科標準治療併用サポート",
        "diagram": "eye_glaucoma_optic_acupoints.jpg",
        "diagram_caption": "図：眼周囲と全身の血流バランスを整える代表的なツボ（攅竹・睛明・太陽・風池・合谷・太衝）の位置目安。",
        "acupoints": [
            ("攅竹（さんちく）", "眉頭の内側の小さなくぼみ", "眼輪筋・前頭筋の緊張緩和と眼周囲血流サポート", "眼球自体は絶対に圧迫しないよう骨の縁を優しく押します。"),
            ("睛明（せいめい）", "目頭のやや上、鼻の付け根のくぼみ", "眼窩周囲の微小循環サポート", "セルフでは強く押さず、専門施術に委ねる繊細な部位です。"),
            ("太陽（たいよう）", "こめかみのくぼみ（目尻と眉尻の中間後方）", "側頭部の緊張緩和・眼精疲労や頭痛の軽減", "指の腹で円を描くように優しくほぐします。"),
            ("風池（ふうち）", "後頭部の生え際のくぼみ", "視神経・後頭葉へつながる頭頸部血流のサポート", "首こりの解消が眼の疲れ軽減に直結します。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "眼周囲の緊張を遠隔から緩める基本穴", "眼科の点眼治療・視野検査を必ず継続してください。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨の間", "東洋医学で「目と深い関わり」を持つ肝経の調整穴", "自律神経の緊張を鎮め、眼への負担を和らげます。"),
        ],
    },
    "eye_dryeye_fatigue": {
        "heading": "ドライアイ・眼精疲労・まぶたのツボ図解",
        "domain_label": "ドライアイ・眼精疲労・VDT症候群・眼瞼下垂/けいれんのケア",
        "diagram": "eye_dryeye_fatigue_acupoints.jpg",
        "diagram_caption": "図：ドライアイ・眼精疲労・まぶたの不調に用いられる代表的なツボ（攅竹・魚腰・絲竹空・太陽・四白・合谷）の位置目安。",
        "acupoints": [
            ("攅竹（さんちく）", "眉頭の内側のくぼみ", "まぶたの重だるさ・涙液分泌の副交感神経サポート", "眼球を押さず眉骨の縁に向かって優しく押します。"),
            ("魚腰（ぎょよう）", "眉毛のちょうど真ん中、黒目の真上", "眼瞼下垂・眼瞼けいれん・まぶたの疲労緩和", "目を閉じて軽く3秒ほど当てて離します。"),
            ("絲竹空（しちくくう）", "眉尻の外側のくぼみ", "目の奥の痛み・側頭部の張り・かすみ目ケア", "コンタクトレンズは外した状態でケアすると安全です。"),
            ("太陽（たいよう）", "こめかみのくぼみ", "デスクワーク後の目の疲れ・こめかみ痛の緩和", "蒸しタオルで目元を温めたあとに行うと効果的です。"),
            ("四白（しはく）", "黒目の真下、頬骨の小さなくぼみ", "下まぶたのけいれん・眼窩下部の循環改善", "皮膚を強くこすらず垂直に軽く触れます。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "頭顔面・目の緊張を和らげる全身調整穴", "仕事中の合間にも手軽に押せるツボです。"),
        ],
    },
    "eye_retina_macula": {
        "heading": "網膜・黄斑・飛蚊症の眼底血流＆全身ツボ図解",
        "domain_label": "加齢黄斑変性・網膜色素変性・飛蚊症の眼科併用・全身血流サポート",
        "diagram": "eye_retina_macula_acupoints.jpg",
        "diagram_caption": "図：眼底循環と全身（肝腎）の土台を支える代表的なツボ（承泣・球後・風池・肝兪・腎兪・光明）の位置目安。",
        "acupoints": [
            ("承泣（しょうきゅう）", "黒目の真下、眼窩の骨の縁", "眼窩深部の血流サポート", "極めて繊細な部位のため自己流の強押しは厳禁です。"),
            ("球後（きゅうご）", "下眼窩縁の外側4分の1のくぼみ", "網膜・視神経領域の循環アプローチで用いられる専門穴", "必ず眼科での定期診察・標準治療と併用します。"),
            ("風池（ふうち）", "後頭部の生え際のくぼみ", "頭頸部〜眼動脈領域の血流と自律神経調整", "首の緊張を解くことで眼周囲の巡りを支えます。"),
            ("肝兪（かんゆ）", "背中の中央やや下、背骨から指2本分外側", "東洋医学で「目に栄養を送る」全身の血流・自律神経穴", "背中を温めるケアも目の疲れに役立ちます。"),
            ("腎兪（じんゆ）", "腰のくびれの高さ、背骨から指2本分外側", "加齢に伴う視機能低下・全身の回復力サポート", "腰背部の冷えを防ぎ全身循環を底上げします。"),
            ("光明（こうめい）", "外くるぶしの上から指6〜7本分上", "古くから目の不調に用いられる下肢の重要遠隔穴", "眼に負担をかけず遠隔から血流バランスを整えます。"),
        ],
    },
    "nose_allergy_smell": {
        "heading": "鼻炎・副鼻腔炎・嗅覚障害の鼻周囲＆全身ツボ図解",
        "domain_label": "アレルギー性鼻炎・慢性副鼻腔炎・嗅覚障害の粘膜・自律神経ケア",
        "diagram": "nose_allergy_smell_acupoints.jpg",
        "diagram_caption": "図：鼻づまり・副鼻腔炎・嗅覚障害に用いられる代表的なツボ（印堂・上迎香・迎香・列缺・合谷・足三里）の位置目安。",
        "acupoints": [
            ("印堂（いんどう）", "左右の眉間の真ん中", "鼻根部の重だるさ・前頭洞周囲のうっ血緩和", "鼻をかんだあと、指の腹で優しく5秒押します。"),
            ("上迎香（じょうげいこう）", "鼻筋の上部、骨と軟骨の境目のくぼみ", "鼻通（びつう）とも呼ばれ、鼻づまり・嗅覚サポートに重要", "左右同時に軽く押さえてゆっくり鼻呼吸します。"),
            ("迎香（げいこう）", "小鼻のすぐ横のくぼみ", "鼻粘膜の血流調整・アレルギー性鼻炎の代表穴", "国際臨床研究でも鼻炎に対して最頻用されるツボです。"),
            ("列缺（れっけつ）", "手首の親指側、シワから指2本分肘寄り", "肺・気道粘膜の過敏性調整・頭頸部の巡り改善", "人差し指で軽く押さえてほぐします。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "鼻炎の炎症バランスと顔面循環の遠隔調整", "花粉症シーズン前の予防ケアにもよく使います。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "免疫バランス・胃腸と粘膜バリアの土台づくり", "全身の体質調整として併用します。"),
        ],
    },
    "throat_nasopharynx": {
        "heading": "後鼻漏・慢性上咽頭炎・のどの違和感のツボ図解",
        "domain_label": "後鼻漏・慢性上咽頭炎・咽喉頭異常感症（梅核気）・発声障害ケア",
        "diagram": "throat_nasopharynx_acupoints.jpg",
        "diagram_caption": "図：のど・上咽頭の違和感と自律神経を整える代表的なツボ（廉泉・扶突・天突・内関・列缺・照海）の位置目安。",
        "acupoints": [
            ("廉泉（れんせん）", "あごの下、のど仏の上のくぼみ", "咽頭周囲の緊張緩和・飲み込みやすさサポート", "のどを強く圧迫せず軽く触れる程度にします。"),
            ("扶突（ふとつ）", "のど仏の高さで首の横（胸鎖乳突筋付近）", "首前面の筋緊張緩和・上咽頭周囲の循環改善", "頸動脈の近くのため自己流の強押しは避けます。"),
            ("天突（てんとつ）", "左右の鎖骨の真ん中のくぼみ", "のどの詰まり感（梅核気）・咳込みの鎮静", "気管に向かって押さず、鎖骨の裏へ斜め下に軽く触れます。"),
            ("内関（ないかん）", "手首の横ジワから指3本分肘寄りの中央", "ストレス性ののどのつかえ感・逆流に伴う不快感緩和", "自律神経の過緊張を鎮める安全な遠隔穴です。"),
            ("列缺（れっけつ）", "手首の親指側、シワから指2本分肘寄り", "咽頭・気道の炎症バランスと粘膜ケア", "照海と組み合わせるとのどの乾燥感に相性が良好です。"),
            ("照海（しょうかい）", "内くるぶしの真下の小さなくぼみ", "のどの乾燥・イガイガ感・夜間咳の遠隔調整穴", "足首を温めながら優しく押します。"),
        ],
    },
    "ear_hearing_tmj": {
        "heading": "突発性難聴・耳管不調・顎関節症のツボ図解",
        "domain_label": "突発性難聴・耳管開放症/狭窄症・顎関節症・食いしばりのケア",
        "diagram": "ear_hearing_tmj_acupoints.jpg",
        "diagram_caption": "図：耳周囲と顎関節の循環・緊張を整える代表的なツボ（耳門・聴宮・聴会・翳風・下関・中渚）の位置目安。",
        "acupoints": [
            ("耳門（じもん）", "耳の穴の前上、口を開けるとくぼむ上部", "耳鳴り・耳閉感・側頭部の緊張緩和", "突発性難聴は発症48時間以内の耳鼻科受診が最優先です。"),
            ("聴宮（ちょうきゅう）", "耳の穴の真ん中の前、口を開けるとくぼむ所", "内耳の微小循環・聴覚過敏や耳管不調のサポート", "口を軽く開けた状態で優しく触れます。"),
            ("聴会（ちょうえ）", "耳たぶの付け根の前、口を開けるとくぼむ下部", "耳周囲と顎関節の連動緊張を和らげるツボ", "強く押し込まず心地よい範囲で刺激します。"),
            ("翳風（えいふう）", "耳たぶのすぐ後ろのくぼみ", "内耳循環と自律神経（迷走神経枝）の調整", "耳周囲の冷え対策に蒸しタオルも有効です。"),
            ("下関（げかん）", "耳の前、頬骨のアーチの下のくぼみ（顎関節部）", "顎関節症・食いしばり・咬筋のこわばり緩和", "口を開閉して痛みが出る場合は無理に動かさず優しく押します。"),
            ("中渚（ちゅうしょ）", "手の甲、薬指と小指の骨の間（拳のくぼみ）", "三焦経の要穴として耳の不調を遠隔から整えるツボ", "耳に触れにくい急性期でも安全に使える遠隔穴です。"),
        ],
    },
    "sys_respiratory_dyspnea": {
        "heading": "呼吸器・喘息・COPD・息苦しさ緩和の全身ツボ図解",
        "domain_label": "気管支喘息・COPD・慢性咳嗽・呼吸困難の標準治療併用サポート",
        "diagram": "sys_respiratory_dyspnea_acupoints.jpg",
        "diagram_caption": "図：呼吸補助筋の緊張緩和と気道調整に用いられる代表的なツボ（定喘・肺兪・膻中・列缺・魚際・合谷）の位置目安。",
        "acupoints": [
            ("定喘（ていぜん）", "首を前に倒して出る背骨のすぐ横", "咳込み・喘鳴・呼吸補助筋の緊張緩和", "背部・胸部への深い刺鍼は避け安全な角度・深さで行います。"),
            ("肺兪（はいゆ）", "肩甲骨の間、第3胸椎のすぐ外側", "気道周囲の自律神経バランス・背部のこわばり緩和", "カイロや温灸で背中を温めるケアも有効です。"),
            ("膻中（だんちゅう）", "左右の乳頭を結ぶ線の中央（胸骨上）", "胸郭の圧迫感・息苦しさに伴う不安の鎮静", "手のひらで優しくさすりながらゆっくり息を吐きます。"),
            ("列缺（れっけつ）", "手首の親指側、シワから指2本分肘寄り", "呼吸器系（肺経）の最重要遠隔穴", "吸入薬などの標準治療を必ず継続したうえで併用します。"),
            ("魚際（ぎょさい）", "親指の付け根のふくらみの縁", "息苦しさ・咳・のどの熱感の緩和（肺がん呼吸困難研究でも使用）", "手首・親指周りを優しく揉みほぐします。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "呼吸困難に伴う不安と全身緊張の軽減", "急激な息苦しさや発作時は直ちに医療機関を受診してください。"),
        ],
    },
    "sys_cardio_circulation": {
        "heading": "循環器・動悸・高血圧・心不全補助の全身ツボ図解",
        "domain_label": "動悸・不整脈・本態性高血圧・慢性心不全の循環器治療併用サポート",
        "diagram": "sys_cardio_circulation_acupoints.jpg",
        "diagram_caption": "図：循環器領域の自律神経・末梢循環を整える代表的なツボ（膻中・曲池・内関・神門・足三里・太衝）の位置目安。",
        "acupoints": [
            ("内関（ないかん）", "手首の横ジワから指3本分肘寄りの中央", "動悸・不整脈・狭心症補助で最も研究されている心包経の要穴", "胸痛・冷や汗・失神がある場合は直ちに救急受診してください。"),
            ("膻中（だんちゅう）", "胸の中央（胸骨上）", "胸のつかえ・交感神経の高ぶり・不安感の鎮静", "強く押さず手のひらで温めながら深呼吸します。"),
            ("神門（しんもん）", "手首の横ジワの小指側のくぼみ", "心拍の落ち着き・緊張性の動悸と不眠ケア", "ペースメーカー使用時は電気鍼を避け手技のみで行います。"),
            ("曲池（きょくち）", "肘を曲げた外側のシワの先端", "血圧変動に伴う上肢・頭部の緊張緩和", "降圧薬・心不全薬の自己判断での中止は厳禁です。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "末梢循環・自律神経バランスとQOL維持", "身体負担の少ない穏やかな低刺激で行います。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨の間", "ストレス性の血圧上昇・末梢血管収縮の緩和", "就寝前の優しい指圧でリラックスを促します。"),
        ],
    },
    "sys_stomach_gerd": {
        "heading": "胃腸・逆流性食道炎・機能性ディスペプシアのツボ図解",
        "domain_label": "胃食道逆流症（GERD）・機能性ディスペプシア・胃痛・吐き気ケア",
        "diagram": "sys_stomach_gerd_acupoints.jpg",
        "diagram_caption": "図：胃運動と迷走神経反射を整える代表的なツボ（巨闕・中脘・天枢・内関・足三里・公孫）の位置目安。",
        "acupoints": [
            ("巨闕（こけつ）", "みぞおちの骨のすぐ下", "胸やけ・みぞおちのつかえ感・横隔膜の緊張緩和", "食後すぐの強い圧迫は逆流を招くため避けます。"),
            ("中脘（ちゅうかん）", "みぞおちとおへその真ん中", "胃もたれ・早期膨満感・胃排出能サポート", "温めながらゆっくり腹式呼吸を行うと緩みます。"),
            ("天枢（てんすう）", "おへその左右それぞれ指3本分外側", "消化管全体の蠕動バランス・お腹の張り軽減", "お腹が冷えやすい方は温灸がおすすめです。"),
            ("内関（ないかん）", "手首の横ジワから指3本分肘寄りの中央", "吐き気・胃酸逆流に伴う不快感・下部食道括約筋の調整", "胃カメラ等の検査前後のセルフケアにも有用です。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "迷走神経を介した胃腸運動調整の最重要穴", "毎日続けやすい胃腸の養生穴です。"),
            ("公孫（こうそん）", "土踏まずの縁、親指の付け根からかかと寄り", "胃痛・胃もたれ・自律神経性の胃腸不調ケア", "内関と組み合わせて優しく押します。"),
        ],
    },
    "sys_kidney_urology": {
        "heading": "腎・泌尿器・排尿トラブル・透析サポートのツボ図解",
        "domain_label": "過活動膀胱・尿失禁・前立腺症状・CKD/透析QOLの標準治療併用ケア",
        "diagram": "sys_kidney_urology_acupoints.jpg",
        "diagram_caption": "図：排尿神経と腎・骨盤循環を整える代表的なツボ（腎兪・次髎・関元・中極・三陰交・太渓）の位置目安。",
        "acupoints": [
            ("腎兪（じんゆ）", "腰のくびれの高さ、背骨から指2本分外側", "腎・泌尿器周囲の自律神経と冷え・倦怠感ケア", "腰部を温めることで夜間頻尿の負担も和らぎます。"),
            ("次髎（じりょう）", "仙骨の2番目のくぼみ（お尻の上中央付近）", "仙髄排尿神経（S2〜S4）を介した膀胱機能調整の最重要穴", "腹圧性・切迫性尿失禁や過活動膀胱の国際RCTでも定番です。"),
            ("関元（かんげん）", "おへそから指4本分下", "下腹部の冷え改善・骨盤内の循環サポート", "排尿を済ませた状態で優しく温めます。"),
            ("中極（ちゅうきょく）", "恥骨のすぐ上、おへそから指5本分下", "膀胱の過敏性・残尿感・頻尿の調整", "強く押し込まず軽く手を当てる・温める程度にします。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "脛骨神経を介した膀胱過活動の鎮静・睡眠の質改善", "透析中の方はシャント腕を避け非透析日に安全に行います。"),
            ("太渓（たいけい）", "内くるぶしとアキレス腱の間", "腎経の原穴として冷え・むくみ・夜間頻尿の土台づくり", "足首を冷やさない工夫と合わせてケアします。"),
        ],
    },
    "sys_women_fertility": {
        "heading": "婦人科・妊活・更年期・産後ケアのツボ図解",
        "domain_label": "月経痛・子宮内膜症・更年期障害・不妊/IVF・産後ケアの全身調整",
        "diagram": "sys_women_fertility_acupoints.jpg",
        "diagram_caption": "図：女性ホルモン・骨盤内血流・自律神経を整える代表的なツボ（気海・関元・血海・三陰交・太渓・太衝）の位置目安。",
        "acupoints": [
            ("気海（きかい）", "おへそから指2本分下", "下腹部の巡り・月経前のだるさや冷えケア", "妊娠中・胚移植後は腹部への強い刺激を避けます。"),
            ("関元（かんげん）", "おへそから指4本分下", "子宮・卵巣周囲の骨盤内血流サポート", "月経痛や冷えが強い時期の温灸が心地よいツボです。"),
            ("血海（けっかい）", "膝のお皿の内上角から指3本分上", "骨盤内のうっ血緩和・月経痛やPMSの軽減", "太もも内側を優しくほぐすように押します。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "女性の三大経絡が交わる最重要穴（月経・更年期・睡眠）", "妊娠初期〜中期は強い刺激を避け専門家に相談してください。"),
            ("太渓（たいけい）", "内くるぶしとアキレス腱の間", "ホルモンバランス・ほてりと冷えの調整・卵巣予備能サポート", "足首を温めながら穏やかに刺激します。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨の間", "PMSのイライラ・ストレス性の周期乱れ・不安感緩和", "深呼吸しながら3〜5秒ずつ押して離します。"),
        ],
    },
    "sys_skin_allergy": {
        "heading": "皮膚・アレルギー・帯状疱疹・脱毛症の全身ツボ図解",
        "domain_label": "アトピー性皮膚炎・慢性蕁麻疹・帯状疱疹痛・円形脱毛症の全身調整",
        "diagram": "sys_skin_allergy_acupoints.jpg",
        "diagram_caption": "図：皮膚のかゆみ・炎症バランスと血流を整える代表的なツボ（百会・曲池・合谷・血海・足三里・三陰交）の位置目安。",
        "acupoints": [
            ("百会（ひゃくえ）", "頭頂部の中央", "頭皮の微小循環（円形脱毛症等）とストレス性のかゆみ鎮静", "皮膚に炎症や傷がある部位は避けて施術します。"),
            ("曲池（きょくち）", "肘を曲げた外側のシワの先端", "アトピー・蕁麻疹のかゆみ（掻痒感）軽減の最重要穴", "皮膚科の標準外用薬・内服薬と併用して行います。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "帯状疱疹痛の鎮痛補助・皮膚バリアの自律神経調整", "帯状疱疹は発症早期の抗ウイルス薬内服が最優先です。"),
            ("血海（けっかい）", "膝のお皿の内上角から指3本分上", "かゆみ・赤み・蕁麻疹の血流バランス調整", "曲池と組み合わせる「曲池・血海」は皮膚ケアの定番です。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "腸管免疫と皮膚の回復力サポート", "慢性的な皮膚トラブルの体質づくりに用います。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "夜間のかゆみによる睡眠障害・乾燥肌の巡り改善", "掻き壊しのある皮膚は避け清潔を保ってケアします。"),
        ],
    },
    "sys_sleep_mental": {
        "heading": "不眠・うつ・不安・自律神経失調の全身ツボ図解",
        "domain_label": "不眠症・抑うつ・不安障害・パニック・自律神経失調の睡眠・心身ケア",
        "diagram": "sys_sleep_mental_acupoints.jpg",
        "diagram_caption": "図：過覚醒を鎮めて深い眠りと心の安定を促す代表的なツボ（百会・印堂・安眠・内関・神門・三陰交）の位置目安。",
        "acupoints": [
            ("百会（ひゃくえ）", "頭頂部の中央", "脳の過覚醒・考えすぎによる緊張の鎮静", "就寝前に指の腹で5秒ほど優しく押して深呼吸します。"),
            ("印堂（いんどう）", "左右の眉間の真ん中", "入眠スイッチの切り替え・不安感や焦燥感の緩和", "目を閉じて軽く触れるだけでも副交感神経が優位になりやすくなります。"),
            ("安眠（あんみん）", "耳の後ろの出っ張った骨の斜め下のくぼみ", "入眠困難・中途覚醒・首の緊張緩和", "枕に当たる首の後ろを温めるとより緩みます。"),
            ("内関（ないかん）", "手首の横ジワから指3本分肘寄りの中央", "胸のざわざわ・動悸・予期不安の鎮静", "不安を感じたときのセルフケアとしても心強いツボです。"),
            ("神門（しんもん）", "手首の横ジワの小指側のくぼみ", "不眠・精神疲労・ストレス応答の安定化", "心療内科・精神科のお薬は必ず主治医の指示通り継続してください。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "足元の冷え改善と睡眠リズムの定着", "お風呂上がりの優しい指圧やレッグウォーマーが有効です。"),
        ],
    },
    "sys_pediatric_shonishin": {
        "heading": "小児はり・発達・夜尿・チックの全身ツボ図解",
        "domain_label": "小児はり（夜尿症・チック・ADHD・喘息・かんむし）の刺さない全身ケア",
        "diagram": "sys_pediatric_shonishin_acupoints.jpg",
        "diagram_caption": "図：刺さない小児はり（撫で刺激）で全身の自律神経を整える代表的なツボ（百会・身柱・天枢・合谷・足三里・三陰交）の位置目安。",
        "acupoints": [
            ("身柱（しんちゅう）", "背中の上部、肩甲骨の間の背骨上", "「ちりけの灸」として古くから小児の夜泣き・体力・呼吸ケアの要穴", "刺さずに専用の小児はりや温かい手で優しく撫でます。"),
            ("百会（ひゃくえ）", "頭頂部の中央", "興奮しやすさ・チックや睡眠リズムの落ち着きサポート", "お子さまが安心できるよう遊びながら優しく触れます。"),
            ("天枢（てんすう）", "おへその左右それぞれ外側", "子どもの腹痛・便秘・食欲不振・乳児疝痛ケア", "時計回りに「の」の字を描くように優しく撫でます。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "緊張の緩和・自律神経バランス調整", "痛みや怖さを与えない皮膚接触刺激で行います。"),
            ("足三里（あしさんり）", "膝の下外側", "体力づくり・胃腸の安定・成長サポート", "小児科の標準治療・発達支援と必ず併用します。"),
            ("三陰交（さんいんこう）", "内くるぶしの上", "夜尿症（おねしょ）・冷え・眠りの深さサポート", "就寝前の親子のスキンシップケアとしてもおすすめです。"),
        ],
    },
    "sys_endo_diabetes_thyroid": {
        "heading": "内分泌・糖尿病・甲状腺・代謝の全身ツボ図解",
        "domain_label": "糖尿病合併症・甲状腺機能異常・メタボリック・代謝異常の併用サポート",
        "diagram": "sys_endo_diabetes_thyroid_acupoints.jpg",
        "diagram_caption": "図：内分泌・代謝バランスと全身循環を支える代表的なツボ（脾兪・腎兪・中脘・関元・足三里・三陰交）の位置目安。",
        "acupoints": [
            ("脾兪（ひゆ）", "背中の中央やや下、背骨の外側", "糖代謝・消化吸収・食後のだるさサポート", "糖尿病・甲状腺疾患は必ず内分泌内科の標準治療を最優先します。"),
            ("腎兪（じんゆ）", "腰のくびれの高さ、背骨の外側", "内分泌系・倦怠感・冷えとむくみの土台づくり", "糖尿病神経障害がある場合は火傷防止のため強い熱刺激を避けます。"),
            ("中脘（ちゅうかん）", "みぞおちとおへその中間", "胃腸機能・糖尿病性胃不全麻痺の自律神経サポート", "食後すぐの強圧迫は避け穏やかに整えます。"),
            ("関元（かんげん）", "おへそから指4本分下", "基礎代謝・下腹部の巡りと疲労回復補助", "低血糖症状（冷や汗・震え）があるときは直ちに補食してください。"),
            ("足三里（あしさんり）", "膝の下外側、指4本分下", "下肢の末梢循環・神経障害予防の全身調整", "足先に傷や潰瘍がある場合は絶対に患部へ施術しません。"),
            ("三陰交（さんいんこう）", "内くるぶしの上から指4本分上", "ホルモン・代謝・睡眠と末梢血流の調和", "厳格な衛生管理と低刺激で安全に継続します。"),
        ],
    },
    "sys_headache_migraine": {
        "heading": "片頭痛・緊張型・群発・連日性頭痛のツボ図解",
        "domain_label": "片頭痛・緊張型頭痛・群発頭痛・慢性連日性頭痛の予防・鎮静ケア",
        "diagram": "sys_headache_migraine_acupoints.jpg",
        "diagram_caption": "図：三叉神経血管系と頭頸部の緊張を鎮める代表的なツボ（百会・率谷・太陽・風池・合谷・太衝）の位置目安。",
        "acupoints": [
            ("百会（ひゃくえ）", "頭頂部の中央", "頭全体の重だるさ・自律神経の過敏鎮静", "突発的な激しい頭痛は直ちに脳神経外科を受診してください。"),
            ("率谷（そっこく）", "耳の一番上から指2本分上の側頭部", "片頭痛のズキズキ感・側頭筋の過緊張緩和", "拍動性頭痛の発作中は強く揉まず静かに冷やすか遠隔穴を使います。"),
            ("太陽（たいよう）", "こめかみのくぼみ", "こめかみの痛み・眼精疲労由来の頭痛ケア", "指の腹で優しく圧迫してゆっくり離します。"),
            ("風池（ふうち）", "後頭部の生え際のくぼみ", "緊張型頭痛・後頭部痛・大後頭神経の緊張緩和", "デスクワークの合間の首ケアに非常に有効です。"),
            ("合谷（ごうこく）", "手の甲、親指と人差し指の間", "三叉神経領域の痛みを遠隔から鎮める国際標準穴", "片頭痛の予兆を感じたときにも安全に押せます。"),
            ("太衝（たいしょう）", "足の甲、親指と人差し指の骨の間", "ストレス・気圧変化・月経関連の頭痛予防サポート", "頭痛専門医の予防薬治療と並行して継続します。"),
        ],
    },
}


def resolve_specialized_acupoint_profile(d: dict, base_ev: dict) -> dict:
    """全1,200疾患について、カテゴリ＋疾患名・キーワードから最適な専門ツボ図解（56種体制）を返す。"""
    cat = d.get("category", "")
    text = f"{d.get('name', '')} {d.get('keywords', '')} {d.get('slug', '')}"

    # 1. 筋肉、骨のお悩みは5部位別ルーティング（既存関数を優先）
    if cat == "筋肉、骨のお悩み":
        return resolve_muscle_acupoint_profile(d, base_ev)

    # 2. がんの種類（6種の症状別・部位別専門図解）
    if cat == "がんの種類" or any(k in text for k in ["がん", "癌", "化学療法", "抗がん剤", "放射線誘発", "頸部郭清", "アロマターゼ"]):
        if any(k in text for k in ["しびれ", "末梢神経", "CIPN", "ニューロパチー"]):
            key = "cancer_cipn_numbness"
        elif any(k in text for k in ["悪心", "嘔吐", "CINV", "食欲不振", "悪液質", "CIA", "吐き気"]):
            key = "cancer_cinv_anorexia"
        elif any(k in text for k in ["倦怠感", "疲労", "CRF", "ケモブレイン", "白血球"]):
            key = "cancer_fatigue_crf"
        elif any(k in text for k in ["口腔乾燥", "口内炎", "口腔粘膜炎", "頭頸部", "頸部郭清", "嚥下"]):
            key = "cancer_headneck_xerostomia"
        elif any(k in text for k in ["ホットフラッシュ", "ホルモン療法", "アロマターゼ", "関節痛", "ADT"]):
            key = "cancer_hormone_hotflash"
        elif any(k in text for k in ["呼吸困難", "息苦しさ"]):
            key = "sys_respiratory_dyspnea"
        elif any(k in text for k in ["不眠", "抑うつ", "不安"]):
            key = "sys_sleep_mental"
        else:
            key = "cancer_pain_palliative"
        merged = dict(base_ev)
        merged.update(SPECIALIZED_ACUPOINT_PROFILES[key])
        return merged

    # 3. 難病系・神経・脳神経の専門図解
    if any(k in text for k in ["パーキンソン", "本態性振戦", "振戦", "ジストニア"]):
        key = "rare_parkinson"
    elif any(k in text for k in ["脊髄小脳", "小脳", "平衡障害", "歩行障害", "多系統萎縮"]):
        key = "rare_cerebellar_balance"
    elif any(k in text for k in ["脳卒中", "脳梗塞", "脳出血", "嚥下障害", "構音障害", "失語症", "片麻痺", "痙縮"]):
        key = "rare_stroke_swallow"
    elif any(k in text for k in ["多発性硬化症", "重症筋無力症", "ギラン", "ALS", "筋萎縮性", "視神経脊髄炎", "ベーチェット", "全身性エリテマトーデス", "SLE", "強皮症", "皮膚筋炎"]):
        key = "rare_ms_mg_systemic"
    elif any(k in text for k in ["リウマチ", "膠原病", "強直性脊椎炎", "シェーグレン", "線維化"]):
        key = "rare_rheumatoid_immune"
    elif any(k in text for k in ["潰瘍性大腸炎", "クローン病", "過敏性腸", "IBS", "便秘", "下痢", "イレウス"]):
        key = "rare_ibd_gut_immune"
    elif any(k in text for k in ["線維筋痛症", "慢性疲労", "CFS", "ロングCOVID", "コロナ後遺症", "筋筋膜性疼痛"]):
        key = "rare_fibromyalgia_cfs"
    elif any(k in text for k in ["CRPS", "複合性局所", "帯状疱疹後神経痛", "視床痛", "中枢性疼痛", "末梢神経障害", "しびれ", "神経痛"]):
        key = "rare_crps_neuropathy"
    elif any(k in text for k in ["メニエール", "めまい", "眩暈", "前庭", "頭鳴り", "耳鳴り", "良性発作性"]):
        key = "rare_meniere_vertigo"
    elif any(k in text for k in ["顔面神経麻痺", "ベル麻痺", "三叉神経痛", "顔面けいれん", "ラムゼイハント"]):
        key = "rare_facial_trigeminal"
    elif any(k in text for k in ["片頭痛", "緊張型頭痛", "群発頭痛", "連日性頭痛", "薬物乱用頭痛", "頭痛"]):
        key = "sys_headache_migraine"
    # 4. 眼の専門図解（3種）
    elif cat == "眼のお悩み" or any(k in text for k in ["緑内障", "視神経", "ドライアイ", "眼精疲労", "黄斑", "網膜", "飛蚊症", "眼瞼", "複視", "弱視", "白内障"]):
        if any(k in text for k in ["緑内障", "視神経", "眼圧"]):
            key = "eye_glaucoma_optic"
        elif any(k in text for k in ["黄斑", "網膜", "飛蚊症", "ぶどう膜", "視力", "中心性"]):
            key = "eye_retina_macula"
        else:
            key = "eye_dryeye_fatigue"
    # 5. 鼻・喉・耳・顎の専門図解（3種）
    elif any(k in text for k in ["鼻炎", "花粉症", "副鼻腔炎", "蓄膿", "嗅覚", "鼻づまり", "鼻閉"]):
        key = "nose_allergy_smell"
    elif any(k in text for k in ["後鼻漏", "上咽頭炎", "梅核気", "咽頭", "のど", "喉", "声帯", "発声", "吃逆", "しゃっくり"]):
        key = "throat_nasopharynx"
    elif cat == "耳鼻咽喉のお悩み" or any(k in text for k in ["難聴", "耳管", "耳閉", "顎関節", "食いしばり", "舌痛症", "歯科", "抜歯"]):
        key = "ear_hearing_tmj"
    # 6. 呼吸器・循環器・消化器・腎泌尿器・婦人科・皮膚・小児・内分泌・睡眠メンタル
    elif any(k in text for k in ["喘息", "COPD", "気管支", "咳", "呼吸", "肺炎", "間質性"]):
        key = "sys_respiratory_dyspnea"
    elif cat == "循環器のお悩み" or any(k in text for k in ["心不全", "狭心症", "心房細動", "不整脈", "動悸", "高血圧", "低血圧", "起立性"]):
        key = "sys_cardio_circulation"
    elif cat == "消化器のお悩み" or any(k in text for k in ["逆流性", "胃炎", "胃潰瘍", "ディスペプシア", "胃不全麻痺", "胃内視鏡", "胃カメラ", "吐き気", "悪心"]):
        key = "sys_stomach_gerd"
    elif cat == "泌尿器のお悩み" or any(k in text for k in ["腎臓", "CKD", "透析", "尿失禁", "尿閉", "過活動膀胱", "膀胱", "前立腺", "頻尿", "腎疝痛", "ED", "早漏"]):
        key = "sys_kidney_urology"
    elif cat == "女性のお悩み" or any(k in text for k in ["月経", "生理", "子宮", "卵巣", "更年期", "不妊", "体外受精", "IVF", "採卵", "妊娠", "産後", "つわり", "逆子", "分娩", "帝王切開", "婦人科"]):
        key = "sys_women_fertility"
    elif cat == "皮膚のお悩み" or any(k in text for k in ["アトピー", "蕁麻疹", "湿疹", "乾癬", "帯状疱疹", "脱毛", "ニキビ", "掌蹠", "掻痒", "かゆみ"]):
        key = "sys_skin_allergy"
    elif cat == "子供のお悩み" or any(k in text for k in ["小児", "夜尿", "チック", "トゥレット", "ADHD", "自閉", "乳児", "脳性麻痺"]):
        key = "sys_pediatric_shonishin"
    elif cat == "内分泌のお悩み" or any(k in text for k in ["糖尿病", "甲状腺", "バセドウ", "橋本", "痛風", "高尿酸", "肥満", "脂質"]):
        key = "sys_endo_diabetes_thyroid"
    elif cat in ("自律神経のお悩み", "特に対応することが多い症状") or any(k in text for k in ["不眠", "睡眠", "うつ", "不安", "パニック", "PTSD", "自律神経", "禁煙", "シバリング", "ICU", "せん妄", "認知"]):
        key = "sys_sleep_mental"
    else:
        return base_ev

    merged = dict(base_ev)
    merged.update(SPECIALIZED_ACUPOINT_PROFILES[key])
    return merged



# ==============================================================================
# 部位・カテゴリ内 自動バリエーションプール（300種段階拡張対応）
# 同じ部位・カテゴリの疾患ページごとに決定論的ランダム（slugハッシュ）で
# 最適なツボ図解バリエーションを自動割り当て（部位ミスマッチ防止＋視覚的鮮度アップ）
# ==============================================================================
ACUPOINT_VARIATION_POOLS: dict[str, list[tuple[str, str]]] = {
    "muscle_lowerback": [
        ("var_muscle_lowerback_01.jpg", "腰背部・腰痛の主要ツボ（腎兪・大腸兪・志室・委中）"),
        ("var_muscle_sciatica_02.jpg", "殿部・下肢後面（坐骨神経）の主要ツボ（環跳・殷門・承山・崑崙）"),
        ("rijokin_acupoints.jpg", "腰殿部・坐骨神経まわりの主要ツボ図解"),
        ("var_muscle_sacroiliac_15.jpg", "仙腸関節・骨盤の主要ツボ（大腸兪・次髎・胞肓・環跳）"),
        ("var_muscle_lumbar_stenosis_24.jpg", "腰・太もも前面の主要ツボ（腎兪・大腸兪・伏兎・足三里）"),
        ("var_muscle_piriformis_28.jpg", "梨状筋・お尻の痛みの主要ツボ（秩辺・環跳・居髎・委中）"),
        ("var_muscle_lumbar_acute_31.jpg", "ぎっくり腰・急性腰痛の主要ツボ（腎兪・委中・腰痛点・後渓）"),
        ("var_muscle_erector_39.jpg", "腰背部・脊柱起立筋の主要ツボ（命門・腎兪・気海兪・関元兪）"),
        ("var_muscle_pelvis_glute_40.jpg", "骨盤・殿部緊張の主要ツボ（上髎・次髎・胞肓・承扶）"),
        ("var_muscle_back_calf_41.jpg", "腰・ふくらはぎ連動の主要ツボ（大腸兪・殷門・委中・飛揚）"),
    ],
    "muscle_neck_shoulder": [
        ("muscle_neck_shoulder_acupoints.jpg", "首・肩・肩甲骨周囲の代表的なツボ位置"),
        ("var_muscle_neck_cervical_04.jpg", "頚椎・首肩こりの主要ツボ（風池・天柱・大椎・肩井）"),
        ("var_muscle_shoulder_frozen_03.jpg", "肩関節・五十肩の主要ツボ（肩髃・天宗・肩貞・肩井）"),
        ("var_muscle_thoracic_back_07.jpg", "背部痛・肋間神経痛の主要ツボ（肺兪・心兪・膈兪・膏肓）"),
        ("var_muscle_rib_chest_12.jpg", "胸郭・巻き肩ケアの主要ツボ（雲門・中府・極泉・膻中）"),
        ("var_muscle_jaw_tmj_11.jpg", "顎関節・食いしばりの主要ツボ（聴宮・下関・頬車・合谷）"),
        ("var_muscle_whiplash_16.jpg", "むちうち・ストレートネックの主要ツボ（風池・大椎・肩外兪・後渓）"),
        ("var_muscle_scapula_wing_20.jpg", "肩甲骨・背中の張りの主要ツボ（肺兪・膏肓・天宗・肩貞）"),
        ("var_muscle_upper_trap_25.jpg", "僧帽筋・肩甲挙筋の主要ツボ（天柱・肩中兪・肩井・後渓）"),
        ("var_muscle_rotator_cuff_27.jpg", "腱板・肩の引っかかりの主要ツボ（巨骨・肩髃・肩髎・臂臑）"),
        ("var_muscle_neck_disk_33.jpg", "頚椎ヘルニア・腕のしびれの主要ツボ（大椎・肩井・曲池・合谷）"),
        ("var_muscle_midback_37.jpg", "背骨・みぞおち裏の主要ツボ（膈兪・肝兪・脾兪・委中）"),
        ("var_muscle_wryneck_42.jpg", "寝違え・首の回旋痛の主要ツボ（風池・天柱・落枕・後渓）"),
        ("var_muscle_tos_clavicle_43.jpg", "胸郭出口・鎖骨下の主要ツボ（欠盆・雲門・中府・尺沢）"),
        ("var_muscle_rhomboid_44.jpg", "肩甲骨内側・猫背ケアの主要ツボ（大杼・風門・厥陰兪・膏肓）"),
    ],
    "muscle_elbow_wrist": [
        ("muscle_elbow_wrist_acupoints.jpg", "肘・前腕・手首の代表的なツボ位置"),
        ("var_muscle_wrist_hand_06.jpg", "手首・腱鞘炎の主要ツボ（合谷・陽渓・外関・大陵）"),
        ("var_muscle_elbow_golf_10.jpg", "肘内側・前腕の主要ツボ（曲沢・少海・内関・神門）"),
        ("var_muscle_finger_thumb_14.jpg", "ばね指・手指ケアの主要ツボ（魚際・合谷・陽渓・外関）"),
        ("var_muscle_wrist_carpal_19.jpg", "手根管・手のしびれの主要ツボ（内関・大陵・労宮・合谷）"),
        ("var_muscle_tennis_forearm_21.jpg", "テニス肘・前腕伸筋の主要ツボ（肘髎・曲池・手三里・外関）"),
        ("var_muscle_wrist_tfcc_30.jpg", "手首小指側・TFCCケアの主要ツボ（陽谷・養老・支正・外関）"),
        ("var_muscle_elbow_biceps_35.jpg", "上腕・肘前面の張りの主要ツボ（手五里・尺沢・曲沢・合谷）"),
        ("var_muscle_thumb_dequervain_38.jpg", "親指腱鞘炎・ドケルバン病の主要ツボ（合谷・陽渓・列缺・偏歴）"),
        ("var_muscle_forearm_pc_45.jpg", "前腕疲労・PC作業痛の主要ツボ（曲池・手三里・温溜・陽池）"),
        ("var_muscle_finger_joint_46.jpg", "手指のこわばり・関節の主要ツボ（八邪・合谷・中渚・外関）"),
        ("var_muscle_triceps_elbow_47.jpg", "肘後面・上腕三頭筋の主要ツボ（天井・清冷淵・小海・支溝）"),
    ],
    "muscle_hip_knee": [
        ("muscle_hip_knee_acupoints.jpg", "股関節・膝周囲の代表的なツボ位置"),
        ("var_muscle_knee_osteo_05.jpg", "膝関節・膝痛の主要ツボ（血海・梁丘・陽陵泉・足三里）"),
        ("var_muscle_hip_groin_09.jpg", "股関節・鼠径部の主要ツボ（居髎・環跳・髀関・陽陵泉）"),
        ("var_muscle_hamstring_13.jpg", "太もも裏・下肢後面の主要ツボ（承扶・殷門・委中・承筋）"),
        ("var_muscle_groin_adductor_22.jpg", "内もも・鼠径部の主要ツボ（箕門・血海・陰陵泉・三陰交）"),
        ("var_muscle_knee_back_26.jpg", "膝裏の張り・柔軟性の主要ツボ（委中・委陽・陰谷・承山）"),
        ("var_muscle_osgood_knee_32.jpg", "スポーツ膝痛・オスグッドの主要ツボ（梁丘・犢鼻・足三里・陽陵泉）"),
        ("var_muscle_hip_bursa_36.jpg", "大転子・お尻外側の主要ツボ（居髎・環跳・風市・陽陵泉）"),
        ("var_muscle_sciatica_02.jpg", "殿部・下肢後面の主要ツボ（環跳・殷門・承山・崑崙）"),
        ("var_muscle_quadriceps_48.jpg", "太もも前面・大腿四頭筋の主要ツボ（髀関・伏兎・梁丘・足三里）"),
        ("var_muscle_itband_49.jpg", "ランナー膝・腸脛靭帯の主要ツボ（風市・中瀆・膝陽関・陽陵泉）"),
        ("var_muscle_knee_medial_50.jpg", "膝内側・鵞足炎ケアの主要ツボ（血海・曲泉・陰陵泉・三陰交）"),
    ],
    "muscle_ankle_foot": [
        ("muscle_ankle_foot_acupoints.jpg", "足首・足部の代表的なツボ位置"),
        ("var_muscle_plantar_calf_08.jpg", "足底・ふくらはぎの主要ツボ（湧泉・承山・太渓・照海）"),
        ("var_muscle_calf_cramp_17.jpg", "こむら返り・アキレス腱の主要ツボ（委中・承筋・承山・崑崙）"),
        ("var_muscle_bunions_toe_18.jpg", "外反母趾・足指ケアの主要ツボ（太白・公孫・行間・解渓）"),
        ("var_muscle_ankle_sprain_23.jpg", "足首捻挫・関節安定の主要ツボ（解渓・丘墟・崑崙・申脈）"),
        ("var_muscle_shin_splint_29.jpg", "シンスプリント・すね痛の主要ツボ（陰陵泉・漏谷・三陰交・承山）"),
        ("var_muscle_heel_spur_34.jpg", "かかと痛・足底腱膜の主要ツボ（太渓・水泉・崑崙・湧泉）"),
        ("var_muscle_achilles_51.jpg", "アキレス腱・かかと周囲の主要ツボ（太渓・水泉・崑崙・僕参）"),
        ("var_muscle_foot_arch_52.jpg", "土踏まず・足裏疲労の主要ツボ（照海・然谷・公孫・湧泉）"),
        ("var_muscle_ankle_front_53.jpg", "足首前面・すね外側の主要ツボ（足三里・豊隆・解渓・衝陽）"),
    ],
    "muscle_general": [
        ("var_muscle_lowerback_01.jpg", "腰背部・体幹の主要ツボ（腎兪・大腸兪・志室・委中）"),
        ("var_muscle_thoracic_back_07.jpg", "背部・胸郭まわりの主要ツボ（肺兪・心兪・膈兪・膏肓）"),
        ("var_muscle_neck_cervical_04.jpg", "頚椎・首肩こりの主要ツボ（風池・天柱・大椎・肩井）"),
        ("var_muscle_knee_osteo_05.jpg", "膝・下肢の主要ツボ（血海・梁丘・陽陵泉・足三里）"),
        ("var_muscle_plantar_calf_08.jpg", "足底・下肢循環の主要ツボ（湧泉・承山・太渓・照海）"),
    ],
    "neuro_headache": [
        ("sys_headache_migraine_acupoints.jpg", "頭痛・片頭痛の代表的なツボ位置"),
        ("var_neuro_headache_temporal_01.jpg", "片頭痛・側頭部痛の主要ツボ（百会・率谷・太陽・風池）"),
        ("var_neuro_dizziness_head_04.jpg", "めまい・脳過敏の主要ツボ（百会・四神聡・完骨・太衝）"),
        ("var_neuro_tension_head_09.jpg", "緊張型頭痛・後頭部の主要ツボ（玉枕・天柱・風池・肩井）"),
        ("var_neuro_weather_pain_13.jpg", "気象病・低気圧不調の主要ツボ（完骨・翳風・内関・豊隆）"),
        ("var_neuro_cluster_head_15.jpg", "群発頭痛・目の奥の痛みの主要ツボ（攢竹・太陽・風池・合谷）"),
        ("var_neuro_migraine_aura_27.jpg", "閃輝暗点・片頭痛の主要ツボ（絲竹空・太陽・率谷・太衝）"),
        ("var_neuro_jaw_headache_35.jpg", "噛みしめ頭痛・こめかみの主要ツボ（太陽・率谷・下関・頬車）"),
        ("var_neuro_occipital_39.jpg", "後頭神経痛・後頭部の主要ツボ（玉枕・天柱・風池・完骨）"),
        ("var_neuro_forehead_head_40.jpg", "前頭部痛・眉間の重さの主要ツボ（神庭・陽白・印堂・合谷）"),
        ("var_neuro_eye_headache_49.jpg", "眼精疲労性頭痛の主要ツボ（睛明・太陽・風池・合谷）"),
    ],
    "neuro_autonomic_sleep": [
        ("jiritsushinkei_acupoints.jpg", "自律神経調整の代表的なツボ位置"),
        ("sys_sleep_mental_acupoints.jpg", "睡眠・メンタルケアの代表的なツボ位置"),
        ("var_neuro_insomnia_calm_02.jpg", "不眠・睡眠ケアの主要ツボ（百会・安眠・神門・内関）"),
        ("var_neuro_autonomic_vagus_03.jpg", "自律神経・迷走神経調整の主要ツボ（膻中・中脘・内関・足三里）"),
        ("var_neuro_stress_chest_05.jpg", "不安感・息苦しさの主要ツボ（膻中・内関・神門・合谷）"),
        ("var_neuro_fatigue_burnout_06.jpg", "慢性疲労・体力回復の主要ツボ（気海・関元・足三里・三陰交）"),
        ("var_neuro_brainfog_10.jpg", "ブレインフォグ・集中力の主要ツボ（百会・神庭・印堂・足三里）"),
        ("var_neuro_depress_mood_12.jpg", "気分の落ち込み・心ケアの主要ツボ（百会・膻中・内関・太衝）"),
        ("var_neuro_night_sweat_14.jpg", "寝汗・ほてり・自律神経の主要ツボ（神門・三陰交・復溜・太渓）"),
        ("var_neuro_ibs_autonomic_16.jpg", "ストレス胃腸・自律神経の主要ツボ（中脘・天枢・内関・足三里）"),
        ("var_neuro_tinnitus_stress_17.jpg", "ストレス性耳鳴り・聴覚過敏の主要ツボ（聴宮・翳風・中渚・太衝）"),
        ("var_neuro_cold_hands_18.jpg", "自律神経の冷え・巡りの主要ツボ（合谷・陽池・三陰交・湧泉）"),
        ("var_neuro_early_wake_21.jpg", "早朝覚醒・浅い眠りの主要ツボ（百会・安眠・太渓・失眠）"),
        ("var_neuro_palpitation_nerve_22.jpg", "自律神経の動悸・緊張の主要ツボ（膻中・巨闕・内関・神門）"),
        ("var_neuro_post_covid_24.jpg", "回復期の倦怠感・自律神経の主要ツボ（百会・気海・足三里・湧泉）"),
        ("var_neuro_orthostatic_25.jpg", "立ちくらみ・起立性調節障害の主要ツボ（百会・内関・足三里・三陰交）"),
        ("var_neuro_vagus_neck_28.jpg", "首こり・迷走神経ケアの主要ツボ（天容・完骨・風池・内関）"),
        ("var_neuro_burnout_mind_29.jpg", "燃え尽き・メンタル疲労の主要ツボ（百会・印堂・膻中・神門）"),
        ("var_neuro_cold_feet_30.jpg", "足の冷え・のぼせ・不眠の主要ツボ（百会・三陰交・太渓・湧泉）"),
        ("var_neuro_panic_breath_33.jpg", "過呼吸・胸の圧迫感の主要ツボ（中府・膻中・内関・太衝）"),
        ("var_neuro_sweating_palm_36.jpg", "手汗・発汗バランスの主要ツボ（労宮・内関・合谷・復溜）"),
        ("var_neuro_sleep_dream_38.jpg", "浅い眠り・心身緊張の主要ツボ（安眠・内関・神門・三陰交）"),
        ("var_neuro_sleep_onset_41.jpg", "入眠困難・寝つき改善の主要ツボ（印堂・安眠・神門・湧泉）"),
        ("var_neuro_autonomic_gut_42.jpg", "自律神経の胃もたれ・吐き気の主要ツボ（中脘・内関・足三里・公孫）"),
        ("var_neuro_anxiety_heart_45.jpg", "胸のざわつき・緊張緩和の主要ツボ（膻中・内関・神門・太衝）"),
        ("var_neuro_deep_relax_50.jpg", "副交感神経・リラックスの主要ツボ（百会・膻中・関元・三陰交）"),
        ("var_neuro_neck_autonomic_51.jpg", "首の自律神経・頭重感の主要ツボ（百会・天柱・風池・合谷）"),
        ("var_neuro_fatigue_morning_52.jpg", "朝の倦怠感・自律神経の主要ツボ（百会・気海・足三里・湧泉）"),
    ],
    "neuro_brain_nerve": [
        ("cat_neuro_acupoints.jpg", "脳神経領域の代表的なツボ位置"),
        ("var_neuro_dizziness_head_04.jpg", "めまい・脳過敏の主要ツボ（百会・四神聡・完骨・太衝）"),
        ("var_neuro_trigeminal_jaw_07.jpg", "顔面・顎・三叉神経の主要ツボ（下関・頬車・翳風・合谷）"),
        ("var_neuro_stroke_limb_08.jpg", "手足のしびれ・神経回復の主要ツボ（曲池・合谷・陽陵泉・太衝）"),
        ("var_neuro_tremor_balance_11.jpg", "ふるえ・歩行バランスの主要ツボ（百会・風池・曲池・陽陵泉）"),
        ("var_neuro_palsy_rehab_19.jpg", "顔面神経・表情筋ケアの主要ツボ（陽白・四白・地倉・頬車）"),
        ("var_neuro_restless_legs_20.jpg", "むずむず脚・下肢違和感の主要ツボ（陽陵泉・足三里・承山・太渓）"),
        ("var_neuro_eyelid_twitch_23.jpg", "眼瞼痙攣・顔面けいれんの主要ツボ（攢竹・絲竹空・四白・太衝）"),
        ("var_neuro_sciatic_numb_26.jpg", "下肢のしびれ・神経ケアの主要ツボ（環跳・陽陵泉・懸鐘・太衝）"),
        ("var_neuro_carpal_nerve_31.jpg", "腕・手の神経しびれの主要ツボ（小海・内関・神門・合谷）"),
        ("var_neuro_vertigo_bppv_32.jpg", "頭位めまい・平衡ケアの主要ツボ（百会・完骨・翳風・足三里）"),
        ("var_neuro_memory_focus_34.jpg", "脳血流・認知ケアの主要ツボ（百会・四神聡・風池・足三里）"),
        ("var_neuro_gait_rehab_37.jpg", "歩行サポート・下肢神経の主要ツボ（足三里・陽陵泉・解渓・太衝）"),
        ("var_neuro_brainfog_10.jpg", "ブレインフォグ・集中力の主要ツボ（百会・神庭・印堂・足三里）"),
        ("var_neuro_headache_temporal_01.jpg", "頭頸部・神経調整の主要ツボ（百会・率谷・太陽・風池）"),
        ("var_neuro_nerve_arm_43.jpg", "腕のだるさ・神経疲労の主要ツボ（曲池・手三里・外関・合谷）"),
        ("var_neuro_vertigo_morning_44.jpg", "朝のめまい・ふらつきの主要ツボ（百会・風池・内関・足三里）"),
        ("var_neuro_chronic_pain_46.jpg", "痛み過敏・全身鎮痛の主要ツボ（百会・合谷・足三里・太衝）"),
        ("var_neuro_swallow_nerve_47.jpg", "嚥下・飲み込みケアの主要ツボ（廉泉・天突・風池・照海）"),
        ("var_neuro_balance_ankle_48.jpg", "足元のふらつき・平衡の主要ツボ（陽陵泉・懸鐘・太渓・湧泉）"),
    ],
    "ent_ear": [
        ("ear_hearing_tmj_acupoints.jpg", "耳・聴覚・顎関節周囲の代表的なツボ位置"),
        ("var_ent_tinnitus_ear_01.jpg", "耳鳴り・難聴の主要ツボ（耳門・聴宮・聴会・翳風）"),
        ("var_ent_vertigo_neck_04.jpg", "耳性めまい・平衡感覚の主要ツボ（聴宮・完骨・風池・豊隆）"),
        ("var_ent_ear_fullness_08.jpg", "耳閉感・耳管ケアの主要ツボ（耳門・聴会・完骨・外関）"),
        ("var_ent_ear_ring_night_10.jpg", "夜間耳鳴り・睡眠ケアの主要ツボ（聴宮・翳風・安眠・太渓）"),
        ("var_ent_ear_ache_tmj_13.jpg", "耳・顎連動の不調の主要ツボ（聴宮・下関・翳風・合谷）"),
        ("var_ent_tinnitus_neck_15.jpg", "首こり性耳鳴り・聴覚の主要ツボ（聴宮・完骨・風池・中渚）"),
        ("var_ent_lowtone_ear_16.jpg", "低音難聴・耳の詰まりの主要ツボ（耳門・聴会・翳風・太渓）"),
        ("var_ent_ear_dizziness_17.jpg", "内耳のむくみ・回転性めまいの主要ツボ（聴宮・完骨・内関・豊隆）"),
    ],
    "ent_nose": [
        ("nose_allergy_smell_acupoints.jpg", "鼻・副鼻腔・嗅覚周囲の代表的なツボ位置"),
        ("var_ent_sinus_nose_02.jpg", "鼻炎・副鼻腔炎の主要ツボ（上星・印堂・迎香・合谷）"),
        ("var_ent_smell_taste_05.jpg", "嗅覚・味覚ケアの主要ツボ（印堂・迎香・廉泉・合谷）"),
        ("var_ent_allergy_pollen_07.jpg", "花粉症・目と鼻の主要ツボ（印堂・睛明・迎香・合谷）"),
        ("var_ent_postnasal_09.jpg", "後鼻漏・上咽頭ケアの主要ツボ（印堂・迎香・天突・列缺）"),
        ("var_ent_chronic_sinus_11.jpg", "慢性副鼻腔炎・顔面重圧の主要ツボ（印堂・四白・迎香・合谷）"),
        ("var_ent_nosebleed_dry_14.jpg", "鼻の乾燥・鼻づまりケアの主要ツボ（上星・迎香・列缺・合谷）"),
        ("var_ent_allergy_morning_18.jpg", "朝の鼻水・アレルギー鼻炎の主要ツボ（印堂・迎香・合谷・足三里）"),
        ("var_ent_sinus_head_19.jpg", "鼻づまり・額の重さの主要ツボ（上星・攢竹・迎香・合谷）"),
        ("var_ent_smell_recovery_22.jpg", "嗅覚回復・鼻通りケアの主要ツボ（上星・迎香・風池・合谷）"),
    ],
    "ent_throat": [
        ("throat_nasopharynx_acupoints.jpg", "喉・上咽頭・嚥下周囲の代表的なツボ位置"),
        ("var_ent_throat_voice_03.jpg", "喉の違和感・声ケアの主要ツボ（廉泉・天突・列缺・照海）"),
        ("var_ent_cough_asthma_06.jpg", "長引く咳・気管支ケアの主要ツボ（天突・定喘・肺兪・尺沢）"),
        ("var_ent_voice_strain_12.jpg", "声枯れ・発声疲労ケアの主要ツボ（廉泉・人迎・天突・魚際）"),
        ("var_ent_dry_throat_20.jpg", "喉の乾燥・イガイガケアの主要ツボ（廉泉・天突・魚際・照海）"),
        ("var_ent_throat_swallow_21.jpg", "喉のつかえ・梅核気の主要ツボ（天突・膻中・内関・豊隆）"),
    ],
    "ent_general": [
        ("cat_ent_acupoints.jpg", "耳鼻咽喉領域の代表的なツボ位置"),
        ("var_ent_tinnitus_ear_01.jpg", "耳鳴り・難聴の主要ツボ（耳門・聴宮・聴会・翳風）"),
        ("var_ent_sinus_nose_02.jpg", "鼻炎・副鼻腔炎の主要ツボ（上星・印堂・迎香・合谷）"),
        ("var_ent_throat_voice_03.jpg", "喉の違和感・声ケアの主要ツボ（廉泉・天突・列缺・照海）"),
        ("var_ent_vertigo_neck_04.jpg", "耳性めまい・平衡感覚の主要ツボ（聴宮・完骨・風池・豊隆）"),
    ],
    "eye_general": [
        ("cat_eye_acupoints.jpg", "眼疾患・視神経周囲の代表的なツボ位置"),
        ("eye_dryeye_fatigue_acupoints.jpg", "眼精疲労・ドライアイの代表的なツボ位置"),
        ("eye_glaucoma_optic_acupoints.jpg", "緑内障・視神経血流ケアの代表的なツボ位置"),
        ("eye_retina_macula_acupoints.jpg", "網膜・黄斑ケアの代表的なツボ位置"),
        ("var_eye_strain_front_01.jpg", "眼精疲労・かすみ目の主要ツボ（睛明・攢竹・太陽・合谷）"),
        ("var_eye_dry_orbit_02.jpg", "ドライアイ・まぶた痙攣の主要ツボ（絲竹空・四白・太陽・太渓）"),
        ("var_eye_optic_circulation_03.jpg", "視神経・眼血流ケアの主要ツボ（睛明・風池・光明・太衝）"),
        ("var_eye_macula_systemic_04.jpg", "網膜・全身眼養生の主要ツボ（肝兪・腎兪・足三里・太渓）"),
        ("var_eye_floaters_05.jpg", "飛蚊症・目の重だるさの主要ツボ（攢竹・太陽・風池・肝兪）"),
        ("var_eye_astigmatism_06.jpg", "ピント調節・疲れ目の主要ツボ（魚腰・太陽・風池・合谷）"),
        ("var_eye_glare_photo_07.jpg", "まぶしさ・光過敏の主要ツボ（印堂・太陽・風池・太衝）"),
        ("var_eye_cataract_care_08.jpg", "かすみ目・加齢性眼養生の主要ツボ（睛明・太陽・光明・腎兪）"),
        ("var_eye_blur_evening_09.jpg", "夕方の目のかすみ・眼疲労の主要ツボ（攢竹・太陽・四白・太渓）"),
        ("var_eye_vdt_screen_10.jpg", "スマホ・PC疲れ目ケアの主要ツボ（睛明・太陽・風池・合谷）"),
        ("var_eye_glaucoma_flow_11.jpg", "眼圧・視神経サポートの主要ツボ（太陽・風池・光明・太衝）"),
        ("var_eye_dry_tear_12.jpg", "涙目・ドライアイ養生の主要ツボ（攢竹・絲竹空・四白・太渓）"),
        ("var_eye_retina_care_13.jpg", "網膜・視界クリアケアの主要ツボ（睛明・肝兪・腎兪・足三里）"),
        ("var_eye_eyelid_heavy_14.jpg", "まぶたの重み・眼瞼ケアの主要ツボ（陽白・魚腰・太陽・合谷）"),
        ("var_eye_vision_vital_15.jpg", "視力養生・全身眼ケアの主要ツボ（風池・養老・光明・太渓）"),
    ],
    "digest_general": [
        ("cat_digestive_acupoints.jpg", "消化器領域の代表的なツボ位置"),
        ("sys_stomach_gerd_acupoints.jpg", "胃腸・逆流性食道炎ケアの代表的なツボ位置"),
        ("var_digest_stomach_01.jpg", "胃痛・逆流性食道炎の主要ツボ（中脘・内関・足三里・公孫）"),
        ("var_digest_ibs_bowel_02.jpg", "腸の不調・便秘・下痢の主要ツボ（天枢・大腸兪・上巨虚・合谷）"),
        ("var_digest_liver_biliary_03.jpg", "お腹の張り・肝胆ケアの主要ツボ（期門・肝兪・陽陵泉・太衝）"),
        ("var_digest_nausea_appetite_04.jpg", "吐き気・胃腸虚弱の主要ツボ（中脘・脾兪・内関・足三里）"),
        ("var_digest_constipation_05.jpg", "頑固な便秘・腸活の主要ツボ（天枢・大横・支溝・上巨虚）"),
        ("var_digest_bloating_gas_06.jpg", "お腹のガス・張りケアの主要ツボ（中脘・天枢・気海・足三里）"),
        ("var_digest_reflux_chest_07.jpg", "胸やけ・みぞおちの主要ツボ（膻中・巨闕・中脘・内関）"),
        ("var_digest_diarrhea_08.jpg", "下痢・お腹の冷えケアの主要ツボ（天枢・関元・陰陵泉・足三里）"),
        ("var_digest_gallbladder_09.jpg", "脇腹の張り・消化サポートの主要ツボ（期門・日月・陽陵泉・足三里）"),
        ("var_digest_stomach_10.jpg", "胃痛・胃もたれケアの主要ツボ（中脘・梁門・内関・足三里）"),
        ("var_digest_ibs_11.jpg", "過敏性腸・お腹調整の主要ツボ（天枢・大腸兪・足三里・上巨虚）"),
        ("var_digest_gerd_12.jpg", "逆流性食道炎・胸やけケアの主要ツボ（膻中・中脘・内関・公孫）"),
        ("var_digest_liver_13.jpg", "肝胆・消化疲れケアの主要ツボ（肝兪・胆兪・陽陵泉・太衝）"),
    ],
    "women_general": [
        ("sys_women_fertility_acupoints.jpg", "女性・骨盤内血流ケアの代表的なツボ位置"),
        ("shikyukinshu_acupoints.jpg", "婦人科領域の代表的なツボ位置"),
        ("var_women_pms_pain_01.jpg", "月経痛・PMSの主要ツボ（関元・血海・地機・三陰交）"),
        ("var_women_menopause_02.jpg", "更年期・ほてりケアの主要ツボ（百会・内関・三陰交・太渓）"),
        ("var_women_fertility_03.jpg", "妊活・骨盤内血流の主要ツボ（関元・子宮・腎兪・三陰交）"),
        ("var_women_postpartum_04.jpg", "産後ケア・冷え・むくみの主要ツボ（気海・足三里・三陰交・太渓）"),
        ("var_women_pcos_hormone_05.jpg", "ホルモンバランス・生理不順の主要ツボ（関元・帰来・三陰交・太渓）"),
        ("var_women_pregnancy_care_06.jpg", "マタニティ・つわりとむくみの主要ツボ（内関・足三里・太渓・湧泉）"),
        ("var_women_cold_pelvis_07.jpg", "骨盤の冷え・巡りケアの主要ツボ（気海・関元・次髎・三陰交）"),
        ("var_women_ovulation_08.jpg", "周期バランス・排卵期ケアの主要ツボ（関元・子宮・血海・三陰交）"),
        ("var_women_edema_pms_09.jpg", "生理前のむくみ・頭痛の主要ツボ（太陽・陰陵泉・三陰交・太衝）"),
        ("var_women_pms_10.jpg", "PMS・月経前バランスケアの主要ツボ（関元・血海・三陰交・太衝）"),
        ("var_women_meno_11.jpg", "更年期・自律バランスケアの主要ツボ（百会・膻中・関元・三陰交）"),
        ("var_women_fert_12.jpg", "妊活・子宮血流ケアの主要ツボ（気海・関元・子宮・三陰交）"),
        ("var_women_post_13.jpg", "産後回復・冷えケアの主要ツボ（関元・腎兪・足三里・三陰交）"),
    ],
    "child_general": [
        ("cat_child_acupoints.jpg", "小児はり領域の代表的なツボ位置"),
        ("sys_pediatric_shonishin_acupoints.jpg", "小児はり・発育サポートの代表的なツボ位置"),
        ("var_child_nightcry_01.jpg", "小児はり・夜泣きケアの主要ツボ（百会・身柱・合谷・足三里）"),
        ("var_child_enuresis_02.jpg", "夜尿症・おねしょケアの主要ツボ（関元・腎兪・三陰交・太渓）"),
        ("var_child_asthma_allergy_03.jpg", "小児喘息・呼吸ケアの主要ツボ（定喘・身柱・肺兪・尺沢）"),
        ("var_child_tic_focus_04.jpg", "チック・落ち着き・睡眠の主要ツボ（百会・印堂・神門・太衝）"),
        ("var_child_digestion_05.jpg", "子どものお腹・食欲ケアの主要ツボ（中脘・天枢・身柱・足三里）"),
        ("var_child_growth_sleep_06.jpg", "成長痛・子どもの睡眠の主要ツボ（身柱・足三里・三陰交・湧泉）"),
        ("var_child_allergy_nose_07.jpg", "子どもの鼻炎・呼吸ケアの主要ツボ（印堂・迎香・身柱・合谷）"),
        ("var_child_immunity_08.jpg", "小児はり・風邪予防と免疫の主要ツボ（身柱・肺兪・足三里・湧泉）"),
        ("var_child_headache_09.jpg", "子どもの頭痛・首こりケアの主要ツボ（百会・太陽・風池・合谷）"),
        ("var_child_motion_sick_10.jpg", "乗り物酔い・緊張性腹痛の主要ツボ（中脘・内関・合谷・足三里）"),
        ("var_child_constipation_11.jpg", "子どもの便秘・お腹ケアの主要ツボ（天枢・身柱・大腸兪・足三里）"),
        ("var_child_posture_12.jpg", "子どもの猫背・肩こりケアの主要ツボ（大椎・身柱・肩井・合谷）"),
        ("var_child_skin_atopy_13.jpg", "子どもの肌荒れ・かゆみの主要ツボ（身柱・曲池・合谷・足三里）"),
        ("var_child_sleep_calm_14.jpg", "子どもの寝ぐずり・夜驚ケアの主要ツボ（百会・印堂・身柱・湧泉）"),
        ("var_child_night_cry_15.jpg", "赤ちゃんの夜泣き・小児はりの主要ツボ（百会・印堂・身柱・足三里）"),
        ("var_child_asthma_16.jpg", "小児喘息・咳ケアの主要ツボ（天突・膻中・定喘・肺兪）"),
        ("var_child_nose_17.jpg", "小児鼻炎・鼻づまりケアの主要ツボ（印堂・迎香・風池・合谷）"),
        ("var_child_bowel_18.jpg", "小児の便秘・お腹ケアの主要ツボ（中脘・天枢・気海・足三里）"),
        ("var_child_growth_19.jpg", "成長サポート・体力づくりの主要ツボ（身柱・脾兪・腎兪・足三里）"),
        ("var_child_posture_20.jpg", "姿勢・首肩ほぐしケアの主要ツボ（天柱・大椎・肩井・身柱）"),
        ("var_child_focus_21.jpg", "集中力・落ち着きサポートの主要ツボ（百会・印堂・神門・合谷）"),
        ("var_child_sleep_22.jpg", "ぐっすり睡眠ケアの主要ツボ（百会・安眠・身柱・湧泉）"),
    ],
    "skin_general": [
        ("cat_skin_acupoints.jpg", "皮膚領域の代表的なツボ位置"),
        ("sys_skin_allergy_acupoints.jpg", "皮膚・アレルギーケアの代表的なツボ位置"),
        ("var_skin_atopy_itch_01.jpg", "アトピー・かゆみの主要ツボ（曲池・合谷・血海・三陰交）"),
        ("var_skin_urticaria_02.jpg", "蕁麻疹・アレルギー肌の主要ツボ（曲池・血海・足三里・築賓）"),
        ("var_skin_shingles_03.jpg", "帯状疱疹・神経痛ケアの主要ツボ（夾脊・支溝・合谷・陽陵泉）"),
        ("var_skin_alopecia_04.jpg", "頭皮血流・脱毛ケアの主要ツボ（百会・角孫・風池・太渓）"),
        ("var_skin_acne_face_05.jpg", "肌荒れ・ニキビケアの主要ツボ（曲池・合谷・足三里・三陰交）"),
        ("var_skin_eczema_dry_06.jpg", "乾燥肌・手湿疹ケアの主要ツボ（曲池・合谷・血海・足三里）"),
        ("var_skin_psoriasis_07.jpg", "慢性皮膚トラブル・体質の主要ツボ（曲池・血海・三陰交・太渓）"),
        ("var_skin_rosacea_red_08.jpg", "赤ら顔・ほてり肌ケアの主要ツボ（曲池・合谷・血海・太衝）"),
        ("var_skin_itch_night_09.jpg", "夜のかゆみ・鎮静ケアの主要ツボ（曲池・神門・血海・三陰交）"),
        ("var_skin_atopy_10.jpg", "アトピー・皮膚バリアケアの主要ツボ（曲池・合谷・血海・三陰交）"),
        ("var_skin_hives_11.jpg", "蕁麻疹・赤み鎮静ケアの主要ツボ（膈兪・曲池・血海・足三里）"),
        ("var_skin_acne_12.jpg", "大人ニキビ・皮脂バランスの主要ツボ（曲池・合谷・三陰交・太衝）"),
        ("var_skin_dry_13.jpg", "乾燥・かゆみ肌ケアの主要ツボ（肺兪・血海・三陰交・太渓）"),
        ("var_skin_shingles_14.jpg", "帯状疱疹後の神経ケアの主要ツボ（夾脊・支溝・陽陵泉・足三里）"),
        ("var_skin_scalp_15.jpg", "頭皮・円形脱毛ケアの主要ツボ（百会・風池・腎兪・太渓）"),
    ],
    "cardio_general": [
        ("cat_circulatory_acupoints.jpg", "循環器領域の代表的なツボ位置"),
        ("sys_cardio_circulation_acupoints.jpg", "循環・血流ケアの代表的なツボ位置"),
        ("var_cardio_palpitation_01.jpg", "動悸・息切れケアの主要ツボ（膻中・心兪・内関・神門）"),
        ("var_cardio_bp_cold_02.jpg", "血圧・冷え・末端循環の主要ツボ（合谷・三陰交・太衝・湧泉）"),
        ("var_cardio_edema_leg_03.jpg", "足のむくみ・だるさの主要ツボ（陰陵泉・三陰交・承山・湧泉）"),
        ("var_cardio_chest_breath_04.jpg", "呼吸の浅さ・胸のつかえの主要ツボ（中府・膻中・内関・列缺）"),
        ("var_cardio_raynaud_05.jpg", "指先の冷え・末梢血行の主要ツボ（八邪・合谷・陽池・湧泉）"),
        ("var_cardio_hypotension_06.jpg", "低血圧・朝のだるさの主要ツボ（百会・気海・内関・足三里）"),
        ("var_cardio_circulation_07.jpg", "全身の血流・めぐりの主要ツボ（内関・合谷・足三里・太衝）"),
        ("var_cardio_palp_08.jpg", "動悸・心拍安定ケアの主要ツボ（膻中・内関・神門・足三里）"),
        ("var_cardio_bp_09.jpg", "血圧バランスケアの主要ツボ（風池・曲池・足三里・太衝）"),
        ("var_cardio_cold_10.jpg", "冷え性・末端循環ケアの主要ツボ（関元・三陰交・太渓・湧泉）"),
    ],
    "uro_general": [
        ("cat_urology_acupoints.jpg", "泌尿器領域の代表的なツボ位置"),
        ("sys_kidney_urology_acupoints.jpg", "腎・泌尿器ケアの代表的なツボ位置"),
        ("var_uro_bladder_freq_01.jpg", "頻尿・過活動膀胱の主要ツボ（中極・腎兪・三陰交・太渓）"),
        ("var_uro_kidney_edema_02.jpg", "むくみ・腎ケアの主要ツボ（水分・腎兪・復溜・太渓）"),
        ("var_uro_prostate_pelvic_03.jpg", "骨盤底・残尿感ケアの主要ツボ（関元・中極・次髎・三陰交）"),
        ("var_uro_cystitis_care_04.jpg", "膀胱の違和感・冷えと頻尿の主要ツボ（関元・中極・陰陵泉・三陰交）"),
        ("var_uro_night_urine_05.jpg", "夜間頻尿・睡眠サポートの主要ツボ（関元・腎兪・太渓・湧泉）"),
        ("var_uro_pelvic_pain_06.jpg", "骨盤痛・下腹部ケアの主要ツボ（中極・次髎・陰陵泉・三陰交）"),
        ("var_uro_kidney_vital_07.jpg", "腎の養生・腰のだるさの主要ツボ（腎兪・志室・太渓・湧泉）"),
        ("var_uro_freq_08.jpg", "頻尿・膀胱ケアの主要ツボ（中極・腎兪・膀胱兪・三陰交）"),
        ("var_uro_kidney_09.jpg", "腎の養生・むくみケアの主要ツボ（水分・腎兪・陰陵泉・太渓）"),
        ("var_uro_pelvic_10.jpg", "骨盤底・尿漏れケアの主要ツボ（関元・中極・次髎・三陰交）"),
    ],
    "cancer_general": [
        ("cat_cancer_acupoints.jpg", "がん支持療法の代表的なツボ位置"),
        ("cancer_cinv_anorexia_acupoints.jpg", "がん支持療法・吐き気・食欲ケアのツボ位置"),
        ("cancer_cipn_numbness_acupoints.jpg", "がん支持療法・しびれケアのツボ位置"),
        ("cancer_fatigue_crf_acupoints.jpg", "がん支持療法・倦怠感ケアのツボ位置"),
        ("cancer_pain_palliative_acupoints.jpg", "がん支持療法・緩和ケアのツボ位置"),
        ("var_cancer_chemo_care_01.jpg", "がん支持療法・吐き気と疲労の主要ツボ（中脘・内関・足三里・三陰交）"),
        ("var_cancer_neuropathy_02.jpg", "手足のしびれ・支持療法の主要ツボ（八邪・合谷・太衝・八風）"),
        ("var_cancer_hotflash_03.jpg", "ホルモン療法・ほてりと関節の主要ツボ（曲池・合谷・三陰交・太渓）"),
        ("var_cancer_sleep_calm_04.jpg", "がん支持療法・不安と睡眠の主要ツボ（百会・内関・神門・三陰交）"),
        ("var_cancer_drymouth_05.jpg", "口の渇き・頭頸部支持療法の主要ツボ（廉泉・頬車・合谷・太渓）"),
        ("var_cancer_appetite_06.jpg", "食欲低下・胃腸支持療法の主要ツボ（中脘・脾兪・内関・足三里）"),
        ("var_cancer_edema_lymph_07.jpg", "むくみ・リンパ循環支持療法の主要ツボ（水分・曲池・陰陵泉・三陰交）"),
        ("var_cancer_nausea_08.jpg", "支持療法・吐き気ケアの主要ツボ（中脘・内関・足三里・公孫）"),
        ("var_cancer_numb_09.jpg", "支持療法・手足しびれケアの主要ツボ（合谷・八邪・足三里・太衝）"),
        ("var_cancer_fatigue_10.jpg", "支持療法・倦怠感ケアの主要ツボ（気海・関元・足三里・三陰交）"),
    ],
    "endo_general": [
        ("cat_endo_acupoints.jpg", "内分泌領域の代表的なツボ位置"),
        ("sys_endo_diabetes_thyroid_acupoints.jpg", "内分泌・代謝ケアの代表的なツボ位置"),
        ("var_endo_thyroid_01.jpg", "甲状腺・代謝ケアの主要ツボ（人迎・合谷・三陰交・太渓）"),
        ("var_endo_diabetes_02.jpg", "内分泌・代謝バランスの主要ツボ（脾兪・腎兪・足三里・三陰交）"),
        ("var_endo_fatigue_adrenal_03.jpg", "副腎疲労・朝の不調の主要ツボ（命門・腎兪・気海・足三里）"),
        ("var_endo_metabolism_04.jpg", "代謝低下・むくみ体質の主要ツボ（水分・関元・陰陵泉・足三里）"),
        ("var_endo_sleep_hormone_05.jpg", "ホルモン・睡眠リズムの主要ツボ（百会・神門・三陰交・太渓）"),
        ("var_endo_cold_thyroid_06.jpg", "冷え・代謝サポートの主要ツボ（命門・腎兪・関元・太渓）"),
        ("var_endo_stress_cortisol_07.jpg", "ストレス・内分泌自律調整の主要ツボ（百会・膻中・腎兪・三陰交）"),
        ("var_endo_thyroid_08.jpg", "甲状腺バランスケアの主要ツボ（人迎・天突・合谷・三陰交）"),
        ("var_endo_metab_09.jpg", "代謝・血糖サポートの主要ツボ（脾兪・腎兪・足三里・三陰交）"),
        ("var_endo_adrenal_10.jpg", "副腎疲労・活力ケアの主要ツボ（百会・腎兪・太渓・湧泉）"),
    ],
}


def pick_variation_diagram(d: dict, ev: dict) -> tuple[str, str]:
    """疾患の部位・カテゴリに適合するバリエーションプールから、ページごとに異なる図解を自動選出する。"""
    cat = d.get("category", "")
    text = f"{d.get('name', '')} {d.get('keywords', '')} {d.get('summary', '')}"
    pool_key = None

    if cat == "筋肉、骨のお悩み":
        if any(k in text for k in ["腰", "坐骨", "梨状筋", "脊柱管", "椎間板", "仙腸", "すべり症", "ぎっくり", "骨盤"]):
            pool_key = "muscle_lowerback"
        elif any(k in text for k in ["肘", "手首", "腱鞘炎", "手根管", "ばね指", "指", "前腕", "上顆炎", "テニス肘", "ゴルフ肘"]):
            pool_key = "muscle_elbow_wrist"
        elif any(k in text for k in ["首", "頚", "頸", "肩", "五十肩", "四十肩", "寝違え", "むちうち", "胸郭出口", "背中", "背部", "肋間"]):
            pool_key = "muscle_neck_shoulder"
        elif any(k in text for k in ["膝", "股関節", "大腿", "半月板", "オスグッド", "変形性膝"]):
            pool_key = "muscle_hip_knee"
        elif any(k in text for k in ["足首", "足底", "アキレス", "外反母趾", "ふくらはぎ", "こむら返り", "捻挫", "踵", "モートン"]):
            pool_key = "muscle_ankle_foot"
        else:
            pool_key = "muscle_general"
    elif cat == "眼のお悩み":
        pool_key = "eye_general"
    elif cat == "耳鼻咽喉のお悩み":
        if any(k in text for k in ["耳", "難聴", "メニエール", "めまい", "耳鳴"]):
            pool_key = "ent_ear"
        elif any(k in text for k in ["鼻", "花粉", "副鼻腔", "蓄膿", "嗅覚"]):
            pool_key = "ent_nose"
        elif any(k in text for k in ["喉", "咽頭", "声", "扁桃", "嚥下", "ヒステリー球"]):
            pool_key = "ent_throat"
        else:
            pool_key = "ent_general"
    elif cat in ("自律神経のお悩み", "特に対応することが多い症状"):
        if any(k in text for k in ["頭痛", "片頭痛", "頭鳴", "脳過敏"]):
            pool_key = "neuro_headache"
        else:
            pool_key = "neuro_autonomic_sleep"
    elif cat in ("脳神経のお悩み", "神経のお悩み"):
        if any(k in text for k in ["頭痛", "片頭痛"]):
            pool_key = "neuro_headache"
        elif any(k in text for k in ["顔面", "三叉", "顎"]):
            pool_key = "neuro_brain_nerve"
        else:
            pool_key = "neuro_brain_nerve"
    elif cat == "消化器のお悩み":
        pool_key = "digest_general"
    elif cat == "女性のお悩み":
        pool_key = "women_general"
    elif cat == "子供のお悩み":
        pool_key = "child_general"
    elif cat == "皮膚のお悩み":
        pool_key = "skin_general"
    elif cat == "循環器のお悩み":
        pool_key = "cardio_general"
    elif cat == "泌尿器のお悩み":
        pool_key = "uro_general"
    elif cat == "がんの種類":
        pool_key = "cancer_general"
    elif cat == "内分泌のお悩み":
        pool_key = "endo_general"

    if pool_key and pool_key in ACUPOINT_VARIATION_POOLS:
        candidates = [item for item in ACUPOINT_VARIATION_POOLS[pool_key] if (IMAGES_DIR / item[0]).exists()]
        if candidates:
            h = int(hashlib.md5(d.get("slug", d.get("name", "")).encode("utf-8")).hexdigest(), 16)
            return candidates[h % len(candidates)]

    return ev.get("diagram", ""), ev.get("diagram_caption", f"{cat}の代表的なツボ位置")


def render_plan_b_visual_card(d: dict) -> str:
    """案B：文字だらけSVGを廃止し、冒頭から専門ツボ図解＋4ステップ改善図解＋3要点チップを先出し表示する。"""
    e = html.escape
    ev_base = CATEGORY_EVIDENCE.get(d["category"], {})
    ev = resolve_specialized_acupoint_profile(d, ev_base) if ev_base else {}

    diag_file, diag_caption = pick_variation_diagram(d, ev)
    diag_src = f"../images/diseases/{diag_file}" if diag_file and (IMAGES_DIR / diag_file).exists() else ""

    mech_img_file = CATEGORY_MECH_IMAGE.get(d["category"], "mech_brain_autonomic.jpg")
    mech_img_src = f"../images/diseases/{mech_img_file}" if (IMAGES_DIR / mech_img_file).exists() else f"./mechanisms/{d['slug']}.svg"

    symptoms = split_items(d["symptoms"])
    top_symptom = symptoms[0] if symptoms else d["name"]
    if len(top_symptom) > 22:
        top_symptom = top_symptom[:21] + "…"

    acupoints = ev.get("acupoints", [])
    top_points = "・".join(row[0] for row in acupoints[:2]) if acupoints else "原因部位・経絡調整"

    left_fig = (
        f'<div class="space-y-2">'
        f'<div class="flex items-center justify-between gap-2">'
        f'<span class="text-xs font-bold text-[#16284c]">📍 アプローチする部位・ツボ</span>'
        f'<span class="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-[#faf5eb] text-[#8c6239] border border-[#e5d5b5]">専門ツボ図解</span>'
        f'</div>'
        f'<figure class="rounded-2xl border border-[#e8e3d9] bg-white p-2">'
        f'<img src="{e(diag_src)}" alt="{e(d["name"])}の代表的なツボ図解" class="w-full h-auto rounded-xl" />'
        f'<figcaption class="text-[11px] text-slate-500 mt-1.5 text-center leading-snug">{e(diag_caption)}</figcaption>'
        f'</figure></div>'
        if diag_src else ""
    )

    right_fig = (
        f'<div class="space-y-2">'
        f'<div class="flex items-center justify-between gap-2">'
        f'<span class="text-xs font-bold text-[#16284c]">🔄 鍼灸で整える4ステップ</span>'
        f'<span class="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-[#faf5eb] text-[#8c6239] border border-[#e5d5b5]">改善メカニズム</span>'
        f'</div>'
        f'<figure class="rounded-2xl border border-[#e8e3d9] bg-white p-2">'
        f'<img src="{e(mech_img_src)}" alt="{e(d["name"])}に対する鍼灸の改善ステップ図解" class="w-full h-auto rounded-xl" />'
        f'<figcaption class="text-[11px] text-slate-500 mt-1.5 text-center leading-snug">神経・血流・筋緊張を段階的に整える流れ</figcaption>'
        f'</figure></div>'
    )

    grid_cls = "grid sm:grid-cols-2 gap-4" if left_fig else "grid grid-cols-1 gap-4"

    return f"""
    <section class="bg-white rounded-3xl border border-[#e8e3d9] shadow-sm p-5 sm:p-6 space-y-4">
      <div class="{grid_cls}">
        {left_fig}
        {right_fig}
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5 pt-1">
        <div class="p-3 rounded-2xl bg-[#faf8f4] border border-[#ece6da] text-center">
          <div class="text-[10px] font-bold text-[#8c6239]">よくあるサイン</div>
          <div class="text-xs font-bold text-[#16284c] mt-1 leading-snug">{e(top_symptom)}</div>
        </div>
        <div class="p-3 rounded-2xl bg-[#faf8f4] border border-[#ece6da] text-center">
          <div class="text-[10px] font-bold text-[#8c6239]">代表的な使用ツボ</div>
          <div class="text-xs font-bold text-[#16284c] mt-1 leading-snug">{e(top_points)}</div>
        </div>
        <div class="p-3 rounded-2xl bg-[#faf8f4] border border-[#ece6da] text-center">
          <div class="text-[10px] font-bold text-[#8c6239]">医療機関との併用</div>
          <div class="text-xs font-bold text-[#16284c] mt-1 leading-snug">標準治療・診察と併用OK</div>
        </div>
      </div>
    </section>
    """


# === SUBDOMAIN_EVIDENCE_PAPERS_START ===
SUBDOMAIN_EVIDENCE_PAPERS: dict[str, dict] = {
    # --- 耳鼻咽喉 ---
    "ent_nose": {
        "label": "鼻炎・副鼻腔炎・嗅覚",
        "papers": [
            ["2013年 大規模多施設RCT", "季節性アレルギー性鼻炎 422名（ドイツ）", "8週後の鼻炎QOL（RQLQ）と抗ヒスタミン薬使用量がシャム鍼・無治療群より有意に改善。", "16週以降は群間差が縮小。", "Ann Intern Med (PMID: 23420231)", "https://pubmed.ncbi.nlm.nih.gov/23420231/"],
            ["2024年 メタ解析（TSA付）", "アレルギー性鼻炎 13 RCTs（n=1,186）", "鼻閉・鼻汁などの総鼻症状スコア（TNSS）およびQOL改善を示唆。", "試験間の刺激法・評価時期の異質性が課題。", "Int Forum Allergy Rhinol (PMID: 39017391)", "https://pubmed.ncbi.nlm.nih.gov/39017391/"],
            ["2020年 耳鼻咽喉科レビュー", "鼻炎・ウイルス後嗅覚障害等 30研究", "アレルギー性鼻炎では補助療法として診療ガイドラインでも言及が増加。", "嗅覚障害などは方法論の統一と大規模RCTが必要。", "Ann Otol Rhinol Laryngol (PMID: 32090591)", "https://pubmed.ncbi.nlm.nih.gov/32090591/"],
        ],
    },
    "ent_ear": {
        "label": "耳鳴り・難聴・めまい",
        "papers": [
            ["2021年 メタ解析", "耳鳴り 8 RCTs（n=504）", "耳鳴り苦痛度（THI）や自覚的うるささの軽減傾向を報告。", "シャム対照の設計や長期追跡に課題。", "Acupunct Med (PMID: 32772848)", "https://pubmed.ncbi.nlm.nih.gov/32772848/"],
            ["2026年 ネットワークメタ解析", "突発性難聴 36研究（n=3,360・標準治療併用）", "ステロイド等の標準治療への鍼灸併用で聴力回復率の向上が示唆。", "発症早期の耳鼻科受診が最優先で、研究バイアスに留意。", "Complement Ther Med (PMID: 42918509)", "https://pubmed.ncbi.nlm.nih.gov/42918509/"],
            ["2024年 メタ解析", "メニエール病・めまい", "めまい障害指数（DHI）・耳鳴り（THI）・耳閉感・聴力スコアの改善を示唆。", "盲検化の困難さと長期追跡RCTの不足が限界。", "Front Med (PMID: 39722819)", "https://pubmed.ncbi.nlm.nih.gov/39722819/"],
        ],
    },
    "ent_throat": {
        "label": "喉・嚥下・咽頭・声",
        "papers": [
            ["2021年 メタ解析", "嚥下障害 35研究（n=3,024）", "標準リハビリへの鍼治療併用で嚥下機能評価（SSA・VFSS・WST）が改善。", "評価基準の統一と厳密な多施設RCTが必要。", "Evid Based Complement Alternat Med (PMID: 33531922)", "https://pubmed.ncbi.nlm.nih.gov/33531922/"],
            ["2023年 メタ解析", "嚥下障害（舌鍼・体鍼併用）15研究（n=1,094）", "嚥下機能スコア（SSA）および総合有効率の改善を報告。", "対象や併用療法にばらつきあり。", "Medicine (PMID: 37304013)", "https://pubmed.ncbi.nlm.nih.gov/37304013/"],
            ["2020年 耳鼻咽喉科レビュー", "発声障害・扁桃術後など 30研究", "耳鼻咽喉領域の安全性と補助的有用性を整理。", "発声・咽頭領域はさらなる高品質RCTが必要。", "Ann Otol Rhinol Laryngol (PMID: 32090591)", "https://pubmed.ncbi.nlm.nih.gov/32090591/"],
        ],
    },
    "ent_tmj": {
        "label": "顎関節症・食いしばり",
        "papers": [
            ["2023年 メタ解析", "成人顎関節症 22 RCTs統合（n=471）", "疼痛軽減および開口域改善で対照群を上回る傾向を報告。", "小規模試験が多く長期持続性の検証が必要。", "Medicine (PMID: 37746950)", "https://pubmed.ncbi.nlm.nih.gov/37746950/"],
            ["2018年 IPDメタ解析", "慢性筋骨格痛 39 RCTs（n=20,827）", "慢性疼痛全般でプラセボ効果を超える持続的な疼痛緩和を確認。", "顎関節単独ではなく慢性痛全体の統合解析。", "J Pain (PMID: 29198932)", "https://pubmed.ncbi.nlm.nih.gov/29198932/"],
        ],
    },
    # --- 筋肉・骨 ---
    "muscle_lowerback": {
        "label": "腰痛・坐骨神経痛・骨盤",
        "papers": [
            ["2020年 Cochraneレビュー", "慢性非特異的腰痛 33 RCTs（n=8,270）", "無治療比で疼痛・機能障害が臨床的に有意に改善（即時〜短期）。", "シャム鍼比の差は小さく、エビデンスの質は低〜中等度。", "Cochrane (PMID: 33306198)", "https://pubmed.ncbi.nlm.nih.gov/33306198/"],
            ["2024年 多施設RCT", "慢性坐骨神経痛 216名（6病院・52週追跡）", "4週後の下肢痛（VAS）と機能障害（ODI）がシャム鍼比で有意に改善し52週維持。", "椎間板ヘルニア由来が対象で全腰痛への一般化は注意。", "JAMA Intern Med (PMID: 39401008)", "https://pubmed.ncbi.nlm.nih.gov/39401008/"],
            ["2018年 IPDメタ解析", "慢性筋骨格痛 39 RCTs（n=20,827）", "腰痛・首肩痛等でプラセボ効果を超える持続的鎮痛を確認。", "対照群の設定により効果量が変動。", "J Pain (PMID: 29198932)", "https://pubmed.ncbi.nlm.nih.gov/29198932/"],
        ],
    },
    "muscle_neck_shoulder": {
        "label": "首痛・肩こり・五十肩・背部",
        "papers": [
            ["2025年 メタ解析（TSA付）", "成人頸部痛 26 RCTs（n=3,520）", "通常治療で十分な改善が得られない頸部痛に対し安全かつ有効な選択肢を示唆。", "試験間の異質性が高くエビデンスの質は低〜中等度。", "PubMed (PMID: 41322280)", "https://pubmed.ncbi.nlm.nih.gov/41322280/"],
            ["2016年 Cochraneレビュー", "慢性頸部障害", "治療終了時および短期フォローでシャム鍼より疼痛緩和に中等度の質のエビデンス。", "長期効果のさらなる検証が必要。", "Cochrane (PMID: 27145001)", "https://pubmed.ncbi.nlm.nih.gov/27145001/"],
            ["2024年 メタ解析", "凍結肩（五十肩）13 RCTs（n=1,034）", "疼痛（VAS）や肩関節機能スコア（CMS）の改善を示唆。", "低質のエビデンスであり厳密なRCTが必要。", "Curr Pain Headache Rep (PMID: 39096470)", "https://pubmed.ncbi.nlm.nih.gov/39096470/"],
        ],
    },
    "muscle_elbow_wrist": {
        "label": "肘痛・手首・腱鞘炎・手根管",
        "papers": [
            ["2020年 メタ解析", "外側上顆炎（テニス肘）10 RCTs（n=796）", "薬物・ブロック療法等と比較し臨床有効率で優位な傾向を報告。", "組み入れ試験の質が低く大規模RCTが必要。", "PubMed (PMID: 32318130)", "https://pubmed.ncbi.nlm.nih.gov/32318130/"],
            ["2024年 系統的レビュー", "外側上顆腱障害 47 RCTs", "シャム鍼比で短期的な機能障害スコア軽減（MD -9.1）を報告。", "エビデンス確実性は低くさらなる検証が必要。", "PubMed (PMID: 38402668)", "https://pubmed.ncbi.nlm.nih.gov/38402668/"],
            ["2018年 Cochraneレビュー", "手根管症候群 12試験（n=869）", "軽〜中等度の症状緩和の補助選択肢として評価。", "シャム鍼比では明確な差が乏しく確実性は低い。", "Cochrane (PMID: 30521680)", "https://pubmed.ncbi.nlm.nih.gov/30521680/"],
        ],
    },
    "muscle_hip_knee": {
        "label": "膝関節・股関節・大腿",
        "papers": [
            ["2016年 メタ解析", "変形性膝関節症 10 RCTs", "短期〜中期で膝痛および身体機能の改善を確認。", "シャム対照との効果差や長期持続性に議論あり。", "JBJS Am (PMID: 27655986)", "https://pubmed.ncbi.nlm.nih.gov/27655986/"],
            ["2018年 IPDメタ解析", "変形性関節症を含む慢性痛 39 RCTs（n=20,827）", "変形性関節症の疼痛緩和でプラセボを超える持続効果を確認。", "運動療法や体重管理との併用が基本。", "J Pain (PMID: 29198932)", "https://pubmed.ncbi.nlm.nih.gov/29198932/"],
        ],
    },
    "muscle_ankle_foot": {
        "label": "足首・足底腱膜炎・アキレス腱",
        "papers": [
            ["2017年 系統的レビュー", "足底腱膜炎（かかと痛）", "4〜8週間の短期的な疼痛（VAS）軽減と少ない副作用を報告。", "8週以降の長期効果についてはエビデンス不足。", "Acupunct Med (PMID: 27526703)", "https://pubmed.ncbi.nlm.nih.gov/27526703/"],
            ["2020年 メタ解析", "急性足関節捻挫 17研究", "RICE処置単独と比較し疼痛緩和および回復率向上の傾向を示唆。", "試験の質と異質性から大規模RCTが必要。", "Evid Based Complement Alternat Med (PMID: 33123213)", "https://pubmed.ncbi.nlm.nih.gov/33123213/"],
            ["2014年 Cochraneレビュー", "成人急性足関節捻挫", "保存療法との併用効果を検討。", "研究間の異質性が高く決定的結論には追加検証が必要。", "Cochrane (PMID: 24953665)", "https://pubmed.ncbi.nlm.nih.gov/24953665/"],
        ],
    },
    # --- 眼 ---
    "eye_dryeye": {
        "label": "ドライアイ・眼精疲労",
        "papers": [
            ["2024年 メタ解析", "ドライアイ 16試験（n=1,383・人工涙液併用）", "人工涙液単独比で涙液層破壊時間（TBUT）・シルマー試験・自覚症状が改善。", "点眼治療との併用が前提で盲検化に限界あり。", "Medicine (PMID: 38181299)", "https://pubmed.ncbi.nlm.nih.gov/38181299/"],
            ["2019年 二重盲検RCT", "ドライアイ 49名（米国・6か月追跡）", "OSDI自覚症状スコアがシャム鍼比で有意に改善し6か月持続。", "単施設・小規模であり客観指標の差は限定的。", "Clin Ophthalmol (PMID: 31114151)", "https://pubmed.ncbi.nlm.nih.gov/31114151/"],
        ],
    },
    "eye_glaucoma": {
        "label": "緑内障・眼圧・視神経",
        "papers": [
            ["2020年 Cochraneレビュー", "緑内障・高眼圧症 3 RCTs（n=137）", "眼圧・視野に対する補助的検討を総括。", "確実性は極めて低く、眼科での点眼・手術管理が最優先。", "Cochrane (PMID: 32032457)", "https://pubmed.ncbi.nlm.nih.gov/32032457/"],
            ["2025年 メタ解析", "変性眼疾患 21研究（緑内障・黄斑変性等）", "標準眼科治療との併用で視力・眼血流指標の補助的改善を示唆。", "研究の異質性が高く標準治療の代替にはならない。", "Complement Ther Med (PMID: 40850524)", "https://pubmed.ncbi.nlm.nih.gov/40850524/"],
        ],
    },
    "eye_retina": {
        "label": "網膜・黄斑・視力養生",
        "papers": [
            ["2025年 メタ解析", "変性眼疾患 21研究（黄斑変性・網膜症等）", "眼科標準治療との併用で視機能・眼循環サポートの可能性を示唆。", "エビデンスの質に課題があり眼科定期管理が必須。", "Complement Ther Med (PMID: 40850524)", "https://pubmed.ncbi.nlm.nih.gov/40850524/"],
            ["2024年 メタ解析", "眼表面・涙液環境 16試験（n=1,383）", "眼周囲の血流・涙液安定など自覚的眼疲労の緩和を報告。", "網膜疾患そのものではなく眼環境サポートの参考データ。", "Medicine (PMID: 38181299)", "https://pubmed.ncbi.nlm.nih.gov/38181299/"],
        ],
    },
    # --- 消化器 ---
    "digest_stomach": {
        "label": "胃痛・機能性ディスペプシア・逆流性食道炎",
        "papers": [
            ["2020年 多施設RCT", "機能性ディスペプシア（PDS）278名（5施設）", "4週後の症状改善率83.0%（シャム群51.6%）で12週後まで維持。", "食後愁訴型が中心で器質的胃疾患の除外が前提。", "Ann Intern Med (PMID: 32422066)", "https://pubmed.ncbi.nlm.nih.gov/32422066/"],
            ["2017年 メタ解析", "胃食道逆流症（GERD）12 RCTs（n=1,235）", "PPI単独比で胸やけ・逆流症状の改善と再発抑制傾向を報告。", "試験の質にばらつきがあり標準薬併用が基本。", "Acupunct Med (PMID: 28689187)", "https://pubmed.ncbi.nlm.nih.gov/28689187/"],
            ["2024年 RCT", "食後愁訴症候群（PDS）51名解析", "胃排出能および自律神経バランス指標の改善を報告。", "小規模研究のため大規模追試が必要。", "Chin Med (PMID: 39568071)", "https://pubmed.ncbi.nlm.nih.gov/39568071/"],
        ],
    },
    "digest_bowel": {
        "label": "過敏性腸症候群（IBS）・便秘・下痢",
        "papers": [
            ["2025年 多施設RCT", "下痢型過敏性腸症候群（IBS-D）280名（6病院）", "腹痛および便性状の複合レスポンダー率がシャム鍼比で有意に改善。", "下痢型中心のデータで他病型への拡張は検証継続中。", "Gastroenterology (PMID: 40441496)", "https://pubmed.ncbi.nlm.nih.gov/40441496/"],
            ["2016年 大規模多施設RCT", "慢性重症機能性便秘 1,075名（15病院）", "電鍼8週で週あたり完全自発排便回数がシャム鍼比で有意増加し12週持続。", "器質的腸閉塞や炎症性腸疾患の除外が前提。", "Ann Intern Med (PMID: 27618593)", "https://pubmed.ncbi.nlm.nih.gov/27618593/"],
        ],
    },
    # --- 皮膚 ---
    "skin_atopy": {
        "label": "アトピー性皮膚炎・湿疹・かゆみ",
        "papers": [
            ["2024年 メタ解析", "アトピー性皮膚炎 8 RCTs（n=463）", "EASI重症度スコアおよび掻痒VASの軽減傾向を報告。", "小規模試験が中心で皮膚科外用治療との併用が基本。", "BMJ Open (PMID: 39638592)", "https://pubmed.ncbi.nlm.nih.gov/39638592/"],
            ["2021年 シャム対照RCT", "軽〜中等症アトピー性皮膚炎 36名（韓国）", "SCORADスコアおよびかゆみ指標がシャム鍼比で有意に低下。", "症例数が少なく長期追跡が必要。", "BMC Complement Med Ther (PMID: 33926433)", "https://pubmed.ncbi.nlm.nih.gov/33926433/"],
            ["2023年 メタ解析", "難治性掻痒 7 RCTs（n=504）", "かゆみ強度スコアの軽減を示唆。", "基礎疾患の管理が最優先。", "J Pain Symptom Manage (PMID: 36055470)", "https://pubmed.ncbi.nlm.nih.gov/36055470/"],
        ],
    },
    "skin_urticaria": {
        "label": "蕁麻疹・赤み・かゆみ",
        "papers": [
            ["2023年 多施設3群RCT", "慢性特発性蕁麻疹 330名（3大学病院）", "4週後の週間蕁麻疹活動スコア（UAS7）がシャム鍼・待機群より有意に改善。", "抗ヒスタミン薬等の標準皮膚科治療との併用・相談が基本。", "Ann Intern Med (PMID: 37956431)", "https://pubmed.ncbi.nlm.nih.gov/37956431/"],
            ["2023年 メタ解析", "難治性掻痒 7 RCTs（n=504）", "慢性掻痒に対するかゆみ軽減の補助効果を報告。", "症例背景の異質性に留意。", "J Pain Symptom Manage (PMID: 36055470)", "https://pubmed.ncbi.nlm.nih.gov/36055470/"],
        ],
    },
    "skin_shingles": {
        "label": "帯状疱疹・帯状疱疹後神経痛",
        "papers": [
            ["2019年 メタ解析", "帯状疱疹後神経痛（PHN）系統的レビュー", "疼痛強度（VAS）の軽減と睡眠・QOL改善傾向を報告。", "急性期は抗ウイルス薬の早期投与が最優先。", "J Pain Res (PMID: 31410050)", "https://pubmed.ncbi.nlm.nih.gov/31410050/"],
            ["2023年 メタ解析", "神経障害性疼痛 8 RCTs（n=338）", "神経痛の疼痛スコア軽減で対照群を上回る傾向を示唆。", "症例規模が小さく薬物療法との併用が基本。", "Front Neurol (PMID: 36698895)", "https://pubmed.ncbi.nlm.nih.gov/36698895/"],
        ],
    },
    "skin_acne": {
        "label": "大人ニキビ・尋常性ざ瘡・肌トラブル",
        "papers": [
            ["2018年 メタ解析", "尋常性ざ瘡（ニキビ）12 RCTs", "ガイドライン推奨治療と同等の改善傾向で副作用が少ない可能性を報告。", "組み入れ研究の方法論的質が低く慎重解釈が必要。", "Evid Based Complement Alternat Med (PMID: 29721027)", "https://pubmed.ncbi.nlm.nih.gov/29721027/"],
            ["2024年 メタ解析", "炎症性皮膚疾患（アトピー）8 RCTs（n=463）", "皮膚炎症・かゆみの緩和補助を示唆。", "皮膚科標準治療との併用が前提。", "BMJ Open (PMID: 39638592)", "https://pubmed.ncbi.nlm.nih.gov/39638592/"],
        ],
    },
    # --- 自律神経・脳神経・神経 ---
    "neuro_headache": {
        "label": "片頭痛・緊張型頭痛・慢性頭痛",
        "papers": [
            ["2016年 Cochraneレビュー", "反復性片頭痛 22 RCTs（n=4,985）", "予防薬と同等の発作頻度減少効果を示し、副作用による脱落が少ない。", "シャム鍼比の差は小〜中等度。", "Cochrane (PMID: 27351677)", "https://pubmed.ncbi.nlm.nih.gov/27351677/"],
            ["2017年 多施設RCT", "前兆のない片頭痛 249名（16週追跡）", "真鍼群で片頭痛発作回数がシャム鍼比-1.1回、待機群比-1.8回有意に減少。", "前兆のある片頭痛や二次性頭痛の除外が必要。", "JAMA Intern Med (PMID: 28241154)", "https://pubmed.ncbi.nlm.nih.gov/28241154/"],
            ["2016年 Cochraneレビュー", "頻発・慢性緊張型頭痛", "通常ケア・シャム鍼比で頭痛日数の減少を確認し非薬物選択肢として評価。", "中等度〜低質のエビデンス。", "Cochrane (PMID: 27092807)", "https://pubmed.ncbi.nlm.nih.gov/27092807/"],
        ],
    },
    "neuro_insomnia": {
        "label": "不眠・睡眠障害",
        "papers": [
            ["2022年 ネットワークメタ解析", "原発性不眠症 57 RCTs（n=4,678）", "通常治療比で睡眠の質（PSQI）の有意な改善を示唆。", "エビデンス確実性は中等度〜低。", "Sleep Med Rev (PMID: 35405419)", "https://pubmed.ncbi.nlm.nih.gov/35405419/"],
            ["2020年 メタ解析", "成人不眠症 13研究（n=911）", "睡眠の質（PSQI）スコアの改善を報告。", "プラセボ効果との切り分けや盲検化に課題。", "Sleep Med (PMID: 32005059)", "https://pubmed.ncbi.nlm.nih.gov/32005059/"],
        ],
    },
    "neuro_anxiety_autonomic": {
        "label": "自律神経失調・不安・ストレス・動悸",
        "papers": [
            ["2025年 メタ解析", "全般性不安障害（GAD）14 RCTs（n=968）", "シャム鍼比で不安尺度（HAMA・GAD-7）および睡眠（PSQI）が有意に改善。", "効果量は中等度でプロトコル標準化が課題。", "Front Psychiatry (PMID: 41312341)", "https://pubmed.ncbi.nlm.nih.gov/41312341/"],
            ["2025年 メタ解析", "自律神経・心拍変動（HRV）10 RCTs（n=744）", "副交感神経指標（HF・RMSSD）の上昇と自律神経バランス調整を示唆。", "測定条件のばらつきあり。", "Front Neurosci (PMID: 41230507)", "https://pubmed.ncbi.nlm.nih.gov/41230507/"],
            ["2023年 メタ解析", "手技鍼 vs シャム鍼（自律神経）9 RCTs", "自律神経反応の調整作用を報告。", "短期評価が中心で長期臨床転帰は要検証。", "Complement Ther Med (PMID: 36494036)", "https://pubmed.ncbi.nlm.nih.gov/36494036/"],
        ],
    },
    "brain_facial": {
        "label": "顔面神経麻痺（ベル麻痺）・三叉神経痛",
        "papers": [
            ["2013年 多施設RCT", "ベル麻痺（顔面神経麻痺）338名（ステロイド併用）", "得気（響き）を伴う鍼併用群で6か月後の顔面機能回復率が有意に向上。", "発症早期のステロイド標準治療との併用が前提。", "CMAJ (PMID: 23439629)", "https://pubmed.ncbi.nlm.nih.gov/23439629/"],
            ["2022年 ネットワークメタ解析", "原発性三叉神経痛 58 RCTs（n=4,126）", "標準薬（カルバマゼピン）比または併用で疼痛軽減・有効率改善を示唆。", "組み入れ試験の質にばらつきがあり神経内科管理と併用。", "Front Neurol (PMID: 35237333)", "https://pubmed.ncbi.nlm.nih.gov/35237333/"],
        ],
    },
    "brain_parkinson": {
        "label": "パーキンソン病・ふるえ・運動症状",
        "papers": [
            ["2024年 シャム対照RCT", "パーキンソン病の睡眠障害 78名解析", "真鍼群でパーキンソン病睡眠尺度（PDSS）がシャム鍼比で有意に改善し4週維持。", "神経内科の薬物療法を継続したうえでの併用効果。", "JAMA Netw Open (PMID: 38922617)", "https://pubmed.ncbi.nlm.nih.gov/38922617/"],
            ["2025年 メタ解析", "自律神経・心拍変動（HRV）10 RCTs（n=744）", "非運動症状に関わる自律神経バランスの調整を示唆。", "パーキンソン病単独の長期進行抑制効果は未確立。", "Front Neurosci (PMID: 41230507)", "https://pubmed.ncbi.nlm.nih.gov/41230507/"],
        ],
    },
    "brain_stroke": {
        "label": "脳卒中後遺症・リハビリ・嚥下",
        "papers": [
            ["2016年 Cochraneレビュー", "脳卒中後リハビリ 31 RCTs（n=2,257）", "日常生活動作（ADL）や神経機能スコアの補助的改善を示唆。", "エビデンスの質は低く標準リハビリとの併用が前提。", "Cochrane (PMID: 27562656)", "https://pubmed.ncbi.nlm.nih.gov/27562656/"],
            ["2021年 メタ解析", "脳卒中後嚥下障害 35研究（n=3,024）", "標準リハビリ併用で嚥下機能評価（SSA・VFSS）が改善。", "厳密な盲検化RCTの蓄積が必要。", "Evid Based Complement Alternat Med (PMID: 33531922)", "https://pubmed.ncbi.nlm.nih.gov/33531922/"],
        ],
    },
    "nerve_peripheral": {
        "label": "末梢神経障害・しびれ・神経痛",
        "papers": [
            ["2024年 メタ解析", "化学療法誘発性末梢神経障害（CIPN）21研究（n=2,121）", "手足のしびれ・神経痛スコアおよびQOLの改善を示唆。", "研究間のプロトコル差が大きく標準治療と併用。", "Front Neurol (PMID: 39555482)", "https://pubmed.ncbi.nlm.nih.gov/39555482/"],
            ["2023年 メタ解析", "神経障害性疼痛全般 8 RCTs（n=338）", "疼痛強度の軽減傾向を報告。", "小規模研究が多く薬物療法との併用が基本。", "Front Neurol (PMID: 36698895)", "https://pubmed.ncbi.nlm.nih.gov/36698895/"],
        ],
    },
    # --- 女性 ---
    "women_menstrual": {
        "label": "月経痛・PMS・生理不順",
        "papers": [
            ["2016年 Cochraneレビュー", "原発性月経困難症 42 RCTs（n=4,640）", "無治療・NSAIDs比で月経痛の軽減を示唆。", "研究の質は低〜極低が多く器質的疾患の婦人科確認が前提。", "Cochrane (PMID: 27087494)", "https://pubmed.ncbi.nlm.nih.gov/27087494/"],
            ["2018年 Cochraneレビュー", "PMS・PMDD 5 RCTs（n=277）", "シャム鍼比で月経前の身体・気分症状の軽減傾向を報告。", "小規模試験が多くエビデンスの確実性は低い。", "Cochrane (PMID: 30105749)", "https://pubmed.ncbi.nlm.nih.gov/30105749/"],
        ],
    },
    "women_menopause": {
        "label": "更年期症状・ホットフラッシュ",
        "papers": [
            ["2013年 Cochraneレビュー", "更年期ホットフラッシュ 16 RCTs（n=1,155）", "無治療比でほてりの頻度・重症度軽減およびQOL改善を報告。", "シャム鍼比の差は小さくホルモン補充療法より効果は緩やか。", "Cochrane (PMID: 23897589)", "https://pubmed.ncbi.nlm.nih.gov/23897589/"],
            ["2022年 国内RCT", "更年期女性 48名（日本式鍼7穴×4週）", "更年期症状スコアおよび自律神経自覚症状の改善を報告。", "小規模であり長期追跡が必要。", "Med Acupunct (PMID: 36311889)", "https://pubmed.ncbi.nlm.nih.gov/36311889/"],
        ],
    },
    "women_fertility": {
        "label": "妊活・不妊・PCOS・産前産後",
        "papers": [
            ["2018年 大規模多施設RCT", "体外受精（IVF）848名（豪州・NZ 16施設）", "移植前後の短期間鍼では生児獲得率に有意差なし（18.3% vs 17.8%）。", "不妊治療の代替ではなく、ストレス緩和・体調管理の補助として位置づけ。", "JAMA (PMID: 29800212)", "https://pubmed.ncbi.nlm.nih.gov/29800212/"],
            ["2017年 大規模多施設RCT", "多嚢胞性卵巣症候群（PCOS）1,000名（21施設）", "クロミフェン併用有無にかかわらず鍼による生児獲得率上昇は認めず。", "生殖医療を最優先とし、心身の巡りケアとして併用検討。", "JAMA (PMID: 28655015)", "https://pubmed.ncbi.nlm.nih.gov/28655015/"],
        ],
    },
    # --- 泌尿器 ---
    "uro_incontinence": {
        "label": "尿失禁・尿漏れ・骨盤底",
        "papers": [
            ["2017年 大規模多施設RCT", "腹圧性尿失禁女性 504名（12病院・腰仙部電鍼）", "6週後の1時間パッド試験の尿漏れ量がシャム電鍼比で有意に減少。", "腹圧性尿失禁が対象で骨盤底筋訓練との併用が推奨。", "JAMA (PMID: 28655016)", "https://pubmed.ncbi.nlm.nih.gov/28655016/"],
            ["2022年 Cochraneレビュー", "過活動膀胱 15研究（n=1,395）", "切迫性尿失禁を含む下部尿路症状の緩和可能性を評価。", "エビデンス確実性は低〜中等度。", "Cochrane (PMID: 36148895)", "https://pubmed.ncbi.nlm.nih.gov/36148895/"],
        ],
    },
    "uro_oab": {
        "label": "頻尿・過活動膀胱・夜間頻尿",
        "papers": [
            ["2022年 Cochraneレビュー", "過活動膀胱 15研究（n=1,395）", "シャム鍼比で頻尿・尿意切迫感の改善可能性を報告。", "薬物療法と同等かは不確実で泌尿器科評価が前提。", "Cochrane (PMID: 36148895)", "https://pubmed.ncbi.nlm.nih.gov/36148895/"],
            ["2023年 メタ解析", "過活動膀胱 30研究統合", "排尿回数・切迫感スコアの改善傾向を示唆。", "研究間の異質性が高い。", "Front Neurol (PMID: 36712423)", "https://pubmed.ncbi.nlm.nih.gov/36712423/"],
        ],
    },
    "uro_prostate": {
        "label": "慢性前立腺炎・骨盤痛・排尿違和感",
        "papers": [
            ["2021年 大規模多施設RCT", "慢性前立腺炎/CPPS男性 440名（10病院）", "8週後のNIH-CPSI症状改善率60.6%（シャム群36.8%）で32週まで維持。", "細菌性前立腺炎や悪性疾患の泌尿器科除外が前提。", "Ann Intern Med (PMID: 34399062)", "https://pubmed.ncbi.nlm.nih.gov/34399062/"],
            ["2022年 Cochraneレビュー", "下部尿路・膀胱症状 15研究（n=1,395）", "排尿不快感の補助的緩和を総括。", "泌尿器科標準管理と併用。", "Cochrane (PMID: 36148895)", "https://pubmed.ncbi.nlm.nih.gov/36148895/"],
        ],
    },
    # --- 循環器 ---
    "cardio_bp": {
        "label": "血圧バランス・高血圧補助ケア",
        "papers": [
            ["2007年 シャム対照RCT", "本態性高血圧 140名完了（ドイツ・24時間ABPM）", "6週間の鍼で24時間収縮期血圧が-5.4mmHg低下（シャム群は変化なし）。", "中止後3〜6か月で戻るため降圧薬の代替ではなく併用補助。", "Circulation (PMID: 17548730)", "https://pubmed.ncbi.nlm.nih.gov/17548730/"],
            ["2015年 メタ解析", "高血圧 23 RCTs（n=1,788）", "降圧薬への鍼併用で単独治療より血圧低下幅が大きい傾向。", "試験の質にばらつきがあり主治医管理が必須。", "PLoS One (PMID: 26207806)", "https://pubmed.ncbi.nlm.nih.gov/26207806/"],
        ],
    },
    "cardio_heart": {
        "label": "動悸・狭心症・心拍自律調整",
        "papers": [
            ["2019年 大規模多施設RCT", "慢性安定狭心症 398名解析（標準薬物療法併用）", "標準薬への経穴鍼併用で狭心症発作頻度がシャム鍼比で有意に減少。", "急性冠症候群の除外と循環器専門医の継続管理が必須。", "JAMA Intern Med (PMID: 31355870)", "https://pubmed.ncbi.nlm.nih.gov/31355870/"],
            ["2025年 メタ解析", "心拍変動（HRV）10 RCTs（n=744）", "副交感神経指標の上昇による心拍リズム安定作用を示唆。", "不整脈の診断・治療は循環器科が最優先。", "Front Neurosci (PMID: 41230507)", "https://pubmed.ncbi.nlm.nih.gov/41230507/"],
        ],
    },
    # --- 子供 ---
    "child_enuresis": {
        "label": "小児夜尿症・おねしょ",
        "papers": [
            ["2022年 メタ解析", "小児夜尿症 13試験（n=890）", "夜尿頻度の減少および改善率向上の傾向を報告。", "小児科での生活指導・アラーム療法等との併用が基本。", "Explore (PMID: 34893441)", "https://pubmed.ncbi.nlm.nih.gov/34893441/"],
        ],
    },
    "child_asthma": {
        "label": "小児喘息・呼吸ケア",
        "papers": [
            ["2025年 メタ解析", "小児喘息 16 RCTs（n=1,675・標準治療併用）", "標準薬物治療への併用で発作症状・呼吸機能指標の補助的改善を示唆。", "吸入ステロイド等の小児科標準管理の代替にはならない。", "Front Med (PMID: 40740962)", "https://pubmed.ncbi.nlm.nih.gov/40740962/"],
        ],
    },
    "child_rhinitis": {
        "label": "小児アレルギー性鼻炎・鼻づまり",
        "papers": [
            ["2024年 メタ解析（TSA付）", "小児アレルギー性鼻炎 13 RCTs（n=1,186）", "鼻症状スコアおよびQOLの改善傾向を報告。", "試験間の刺激法の差が大きく小児科・耳鼻科管理と併用。", "Int Forum Allergy Rhinol (PMID: 39017391)", "https://pubmed.ncbi.nlm.nih.gov/39017391/"],
        ],
    },
    "child_colic_general": {
        "label": "小児はり・夜泣き・乳児疝痛・お腹ケア",
        "papers": [
            ["2017年 多施設3群RCT（ACU-COL）", "乳児疝痛・夜泣き 147名（スウェーデン）", "標準育児支援単独比で泣き時間の相対的減少を安全に確認。", "当院では刺さない小児はりでさらに低刺激に対応。", "Acupunct Med (PMID: 28093383)", "https://pubmed.ncbi.nlm.nih.gov/28093383/"],
            ["2018年 IPD系統的レビュー", "乳児疝痛 3 RCTs（n=307）", "盲検化の制約から一律の推奨には慎重姿勢を示唆。", "発熱・脱水など急変時は小児科受診が最優先。", "Acta Paediatr (PMID: 29338487)", "https://pubmed.ncbi.nlm.nih.gov/29338487/"],
            ["2016年 メタ解析", "小児術後悪心嘔吐（PONV）8研究", "内関（PC6）刺激による小児の吐き気軽減を報告。", "術後・吐き気ケアの参考エビデンス。", "Laryngoscope (PMID: 26864736)", "https://pubmed.ncbi.nlm.nih.gov/26864736/"],
        ],
    },
    # --- がん支持療法 ---
    "cancer_pain": {
        "label": "がん支持療法・疼痛緩和",
        "papers": [
            ["2022年 国際診療ガイドライン", "SIO-ASCO合同ガイドライン（227文献レビュー）", "乳がんアロマターゼ阻害薬関連関節痛や一般がん疼痛への鍼治療を推奨。", "主治医（腫瘍内科）と連携した支持療法として実施。", "J Clin Oncol (PMID: 36122322)", "https://pubmed.ncbi.nlm.nih.gov/36122322/"],
            ["2020年 メタ解析", "がん関連疼痛 17 RCTs（n=1,111）", "疼痛強度の軽減および鎮痛薬使用量の減少を報告。", "エビデンス確実性は中等度。", "JAMA Oncol (PMID: 31855257)", "https://pubmed.ncbi.nlm.nih.gov/31855257/"],
        ],
    },
    "cancer_cipn": {
        "label": "がん支持療法・手足のしびれ（CIPN）",
        "papers": [
            ["2024年 メタ解析", "化学療法誘発性末梢神経障害（CIPN）21研究（n=2,121）", "しびれ・神経痛およびQOLの改善傾向を報告。", "抗がん剤治療中の主治医連携が前提。", "Front Neurol (PMID: 39555482)", "https://pubmed.ncbi.nlm.nih.gov/39555482/"],
            ["2020年 3群パイロットRCT", "CIPN 75名（米国MSKがんセンター）", "通常ケア比でしびれ・疼痛スコアの改善を確認。", "パイロット規模のため大規模検証が進行中。", "JAMA Netw Open (PMID: 32159808)", "https://pubmed.ncbi.nlm.nih.gov/32159808/"],
            ["2022年 国際診療ガイドライン", "SIO-ASCO合同ガイドライン", "CIPNに対する補助的選択肢として言及。", "血球減少・感染リスクへの配慮が必要。", "J Clin Oncol (PMID: 36122322)", "https://pubmed.ncbi.nlm.nih.gov/36122322/"],
        ],
    },
    "cancer_cinv": {
        "label": "がん支持療法・吐き気（CINV）・食欲",
        "papers": [
            ["2023年 メタ解析", "化学療法誘発性悪心嘔吐（CINV）38 RCTs（n=2,503）", "制吐薬への鍼灸併用で悪心・嘔吐の軽減を報告。", "標準制吐薬療法との併用が前提。", "Cancer Med (PMID: 37226372)", "https://pubmed.ncbi.nlm.nih.gov/37226372/"],
            ["2022年 国際診療ガイドライン", "SIO-ASCO合同ガイドライン", "がん支持療法における安全な補完ケアの位置づけを整理。", "主治医への共有を推奨。", "J Clin Oncol (PMID: 36122322)", "https://pubmed.ncbi.nlm.nih.gov/36122322/"],
        ],
    },
    "cancer_fatigue": {
        "label": "がん支持療法・倦怠感（CRF）・体力維持",
        "papers": [
            ["2026年 メタ解析", "がん関連倦怠感（CRF）28 RCTs", "対照群比で倦怠感スコア（PFS）の有意な改善を報告。", "全体的な研究の質は低くさらなる高品質RCTが必要。", "PubMed (PMID: 42164120)", "https://pubmed.ncbi.nlm.nih.gov/42164120/"],
            ["2022年 国際診療ガイドライン", "SIO-ASCO合同ガイドライン", "がん治療に伴う心身負担への補完的ケアを評価。", "標準がん治療と連携して実施。", "J Clin Oncol (PMID: 36122322)", "https://pubmed.ncbi.nlm.nih.gov/36122322/"],
        ],
    },
    # --- 内分泌 ---
    "endo_diabetes": {
        "label": "糖尿病・血糖・糖尿病性神経障害",
        "papers": [
            ["2026年 メタ解析", "2型糖尿病 20 RCTs（n=1,479）", "標準治療併用で空腹時血糖・HbA1cの補助的改善を示唆。", "食事・運動・薬物療法が基本であり代替ではない。", "Postgrad Med J (PMID: 41052150)", "https://pubmed.ncbi.nlm.nih.gov/41052150/"],
            ["2025年 メタ解析", "糖尿病性末梢神経障害（DPN）14 RCTs（n=1,169）", "下肢しびれ・神経伝導速度の改善傾向を報告。", "血糖管理とフットケアの併用が必須。", "Curr Pain Headache Rep (PMID: 40220243)", "https://pubmed.ncbi.nlm.nih.gov/40220243/"],
        ],
    },
    "endo_thyroid": {
        "label": "甲状腺疾患・橋本病・ホルモンバランス",
        "papers": [
            ["2024年 メタ解析", "橋本病（慢性甲状腺炎）14 RCTs（n=1,020）", "標準ホルモン補充療法への併用で自己抗体や自覚症状の改善傾向を報告。", "内分泌内科での血液検査・薬物管理が必須。", "Medicine (PMID: 38428856)", "https://pubmed.ncbi.nlm.nih.gov/38428856/"],
        ],
    },
    "endo_obesity": {
        "label": "代謝低下・肥満・生活習慣サポート",
        "papers": [
            ["2024年 メタ解析", "単純性肥満 25 RCTs（n=2,018・生活習慣併用）", "食事・運動指導への併用でBMI・腹囲減少の補助効果を示唆。", "生活習慣改善との併用が前提。", "Diabetes Metab Syndr Obes (PMID: 39582784)", "https://pubmed.ncbi.nlm.nih.gov/39582784/"],
            ["2026年 メタ解析", "糖代謝（2型糖尿病）20 RCTs（n=1,479）", "糖代謝・インスリン抵抗性サポートの可能性を報告。", "内科管理と併用。", "Postgrad Med J (PMID: 41052150)", "https://pubmed.ncbi.nlm.nih.gov/41052150/"],
        ],
    },
}


def resolve_subdomain_papers(d: dict, ev: dict) -> tuple[str, list]:
    """疾患の部位・器官・症状に厳密一致するサブ領域の査読論文リストだけを返す。"""
    cat = d.get("category", "")
    text = f"{d.get('name', '')} {d.get('keywords', '')} {d.get('summary', '')}"
    key = None

    if cat == "耳鼻咽喉のお悩み":
        if any(k in text for k in ["顎関節", "食いしばり", "歯ぎしり", "開口"]):
            key = "ent_tmj"
        elif any(k in text for k in ["鼻", "花粉", "副鼻腔", "蓄膿", "嗅覚", "後鼻漏", "アレルギー性鼻炎"]):
            key = "ent_nose"
        elif any(k in text for k in ["耳", "難聴", "メニエール", "めまい", "耳鳴", "耳管", "前庭"]):
            key = "ent_ear"
        elif any(k in text for k in ["喉", "咽頭", "声", "扁桃", "嚥下", "ヒステリー球", "梅核気", "咳", "気管"]):
            key = "ent_throat"
    elif cat == "筋肉、骨のお悩み":
        if any(k in text for k in ["顎関節", "食いしばり"]):
            key = "ent_tmj"
        elif any(k in text for k in ["腰", "坐骨", "梨状筋", "脊柱管", "椎間板", "仙腸", "すべり症", "ぎっくり", "骨盤"]):
            key = "muscle_lowerback"
        elif any(k in text for k in ["肘", "手首", "腱鞘炎", "手根管", "ばね指", "指", "前腕", "上顆炎", "テニス肘", "ゴルフ肘", "ドケルバン", "TFCC"]):
            key = "muscle_elbow_wrist"
        elif any(k in text for k in ["首", "頚", "頸", "肩", "五十肩", "四十肩", "寝違え", "むちうち", "胸郭出口", "背中", "背部", "肋間", "肩甲骨", "ストレートネック"]):
            key = "muscle_neck_shoulder"
        elif any(k in text for k in ["膝", "股関節", "大腿", "半月板", "オスグッド", "変形性膝", "鵞足", "腸脛"]):
            key = "muscle_hip_knee"
        elif any(k in text for k in ["足首", "足底", "アキレス", "外反母趾", "ふくらはぎ", "こむら返り", "捻挫", "踵", "かかと", "モートン", "シンスプリント"]):
            key = "muscle_ankle_foot"
    elif cat == "眼のお悩み":
        if any(k in text for k in ["緑内障", "眼圧", "視神経"]):
            key = "eye_glaucoma"
        elif any(k in text for k in ["網膜", "黄斑", "白内障", "飛蚊", "視力", "近視"]):
            key = "eye_retina"
        else:
            key = "eye_dryeye"
    elif cat == "消化器のお悩み":
        if any(k in text for k in ["腸", "便秘", "下痢", "過敏性", "IBS", "大腸", "潰瘍性", "クローン", "ガス", "膨満"]):
            key = "digest_bowel"
        else:
            key = "digest_stomach"
    elif cat == "皮膚のお悩み":
        if any(k in text for k in ["蕁麻疹", "じんましん"]):
            key = "skin_urticaria"
        elif any(k in text for k in ["帯状疱疹", "ヘルペス"]):
            key = "skin_shingles"
        elif any(k in text for k in ["ニキビ", "ざ瘡", "吹き出物", "脂漏"]):
            key = "skin_acne"
        else:
            key = "skin_atopy"
    elif cat in ("自律神経のお悩み", "特に対応することが多い症状"):
        if any(k in text for k in ["頭痛", "片頭痛", "頭鳴"]):
            key = "neuro_headache"
        elif any(k in text for k in ["不眠", "睡眠", "寝つき", "中途覚醒", "早朝覚醒"]):
            key = "neuro_insomnia"
        elif any(k in text for k in ["胃", "ディスペプシア", "吐き気", "逆流"]):
            key = "digest_stomach"
        elif any(k in text for k in ["更年期", "ホットフラッシュ"]):
            key = "women_menopause"
        else:
            key = "neuro_anxiety_autonomic"
    elif cat in ("脳神経のお悩み", "神経のお悩み"):
        if any(k in text for k in ["頭痛", "片頭痛"]):
            key = "neuro_headache"
        elif any(k in text for k in ["顔面", "ベル麻痺", "三叉", "眼瞼痙攣"]):
            key = "brain_facial"
        elif any(k in text for k in ["パーキンソン", "ふるえ", "振戦"]):
            key = "brain_parkinson"
        elif any(k in text for k in ["脳卒中", "脳梗塞", "脳出血", "片麻痺", "嚥下"]):
            key = "brain_stroke"
        elif any(k in text for k in ["帯状疱疹"]):
            key = "skin_shingles"
        elif any(k in text for k in ["手根管"]):
            key = "muscle_elbow_wrist"
        elif any(k in text for k in ["不眠", "睡眠"]):
            key = "neuro_insomnia"
        else:
            key = "nerve_peripheral"
    elif cat == "女性のお悩み":
        if any(k in text for k in ["更年期", "ほてり", "ホットフラッシュ", "閉経"]):
            key = "women_menopause"
        elif any(k in text for k in ["妊活", "不妊", "体外受精", "PCOS", "多嚢胞", "妊娠", "つわり", "産後", "逆子"]):
            key = "women_fertility"
        else:
            key = "women_menstrual"
    elif cat == "泌尿器のお悩み":
        if any(k in text for k in ["前立腺", "骨盤痛", "精巣", "ED"]):
            key = "uro_prostate"
        elif any(k in text for k in ["尿失禁", "尿漏れ", "尿もれ", "腹圧性"]):
            key = "uro_incontinence"
        else:
            key = "uro_oab"
    elif cat == "循環器のお悩み":
        if any(k in text for k in ["高血圧", "低血圧", "血圧"]):
            key = "cardio_bp"
        else:
            key = "cardio_heart"
    elif cat == "子供のお悩み":
        if any(k in text for k in ["夜尿", "おねしょ"]):
            key = "child_enuresis"
        elif any(k in text for k in ["喘息", "ぜんそく", "咳", "気管支"]):
            key = "child_asthma"
        elif any(k in text for k in ["鼻炎", "花粉", "鼻づまり", "副鼻腔"]):
            key = "child_rhinitis"
        else:
            key = "child_colic_general"
    elif cat == "がんの種類":
        if any(k in text for k in ["しびれ", "末梢神経", "CIPN"]):
            key = "cancer_cipn"
        elif any(k in text for k in ["吐き気", "悪心", "嘔吐", "食欲", "胃", "食道", "大腸", "膵", "肝"]):
            key = "cancer_cinv"
        elif any(k in text for k in ["倦怠", "疲労", "だる", "白血病", "リンパ腫"]):
            key = "cancer_fatigue"
        else:
            key = "cancer_pain"
    elif cat == "内分泌のお悩み":
        if any(k in text for k in ["甲状腺", "橋本", "バセドウ"]):
            key = "endo_thyroid"
        elif any(k in text for k in ["肥満", "メタボ", "脂質", "コレステロール", "痛風"]):
            key = "endo_obesity"
        else:
            key = "endo_diabetes"

    if key and key in SUBDOMAIN_EVIDENCE_PAPERS:
        info = SUBDOMAIN_EVIDENCE_PAPERS[key]
        return info["label"], info["papers"]
    return ev.get("domain_label", cat), ev.get("papers", [])
# === SUBDOMAIN_EVIDENCE_PAPERS_END ===


def render_category_cards(d: dict) -> str:
    """案B：ツボ詳細一覧・論文エビデンスを折りたたみ収納し、受診最優先サインを見やすく配置する。"""
    ev = CATEGORY_EVIDENCE.get(d["category"])
    if not ev:
        return ""
    ev = resolve_specialized_acupoint_profile(d, ev)
    e = html.escape

    acu_rows = "".join(
        f'<tr><td class="p-3 font-bold text-[#16284c] whitespace-nowrap">{e(row[0])}</td><td class="p-3">{e(row[1])}</td><td class="p-3">{e(row[2])}</td><td class="p-3">{e(row[3])}</td></tr>'
        for row in ev.get("acupoints", [])
    )
    ev_domain_label, matched_papers = resolve_subdomain_papers(d, ev)
    paper_rows_list = []
    if d.get("source_url") and d["source_url"].startswith("http") and "s-treatment.com" not in d["source_url"].lower():
        paper_rows_list.append(
            f'<tr class="bg-[#faf6ee]"><td class="p-3 font-bold text-[#b68d40] whitespace-nowrap">個別疾患・一次出典</td>'
            f'<td class="p-3 font-bold text-[#16284c]">{e(d["name"])}</td>'
            f'<td class="p-3">本疾患に特化した一次論文・公的ガイドラインを参照しています。</td>'
            f'<td class="p-3"><a href="{e(d["source_url"])}" target="_blank" rel="noopener" class="text-[#16284c] underline font-bold">一次文献を確認する</a></td></tr>'
        )
    for p in matched_papers:
        paper_rows_list.append(
            f'<tr><td class="p-3 font-bold text-[#16284c] whitespace-nowrap">{e(p[0])}</td><td class="p-3">{e(p[1])}</td><td class="p-3">{e(p[2])}</td>'
            f'<td class="p-3">{e(p[3])} <a href="{e(p[5])}" target="_blank" rel="noopener" class="text-[#16284c] underline">{e(p[4])}</a></td></tr>'
        )
    paper_rows = "".join(paper_rows_list)

    ref_items = []
    if d.get("source_url") and d["source_url"].startswith("http") and "s-treatment.com" not in d["source_url"].lower():
        ref_items.append(
            f'<li>一次論文・公的出典：<a href="{e(d["source_url"])}" target="_blank" rel="noopener" class="text-[#16284c] underline">{e(d["name"])}の根拠文献</a></li>'
        )
    for p in matched_papers:
        ref_items.append(
            f'<li>{e(p[0])}（{e(p[1])}）— <a href="{e(p[5])}" target="_blank" rel="noopener" class="text-[#16284c] underline">{e(p[4])}</a></li>'
        )

    paper_cnt = len(paper_rows_list)

    return f'''
    <details id="sec-acupoints" class="bg-white rounded-2xl border border-[#e8e3d9] shadow-sm overflow-hidden">
      <summary class="px-5 py-4 flex items-center justify-between gap-3 cursor-pointer hover:bg-[#faf8f4] transition-colors">
        <div>
          <p class="text-[11px] font-bold text-[#b68d40]">04. ツボ詳細・セルフケア一覧</p>
          <h2 class="font-mincho text-sm sm:text-base font-bold text-[#16284c]">{e(ev.get("heading", d["category"] + "のツボ一覧"))}（{len(ev.get("acupoints", []))}穴）</h2>
        </div>
        <span class="chev text-slate-400 shrink-0">▼</span>
      </summary>
      <div class="px-5 pb-5 pt-3 border-t border-[#ece6da] space-y-3">
        <p class="text-xs sm:text-sm text-slate-600 leading-relaxed">
          実際の施術ではお一人おひとりの体質・症状に合わせて選穴しますが、{e(ev["domain_label"])}の臨床でよく用いられる代表的なツボの目安です。
        </p>
        <div class="overflow-x-auto rounded-xl border border-slate-200">
          <table class="w-full min-w-[540px] text-xs sm:text-sm text-left border-collapse">
            <thead class="bg-[#16284c] text-white">
              <tr><th class="p-3">ツボ名</th><th class="p-3">位置の目安</th><th class="p-3">主な狙い</th><th class="p-3">セルフケアの注意点</th></tr>
            </thead>
            <tbody class="divide-y divide-slate-200 text-slate-700 bg-white">{acu_rows}</tbody>
          </table>
        </div>
      </div>
    </details>

    <details id="sec-evidence" class="bg-white rounded-2xl border border-[#e8e3d9] shadow-sm overflow-hidden">
      <summary class="px-5 py-4 flex items-center justify-between gap-3 cursor-pointer hover:bg-[#faf8f4] transition-colors">
        <div>
          <p class="text-[11px] font-bold text-[#b68d40]">05. 医学的根拠・国際査読論文（PubMed / Cochrane）</p>
          <h2 class="font-mincho text-sm sm:text-base font-bold text-[#16284c]">科学的エビデンス・出典リンク一覧（{paper_cnt}件）</h2>
        </div>
        <span class="chev text-slate-400 shrink-0">▼</span>
      </summary>
      <div class="px-5 pb-5 pt-3 border-t border-[#ece6da] space-y-4">
        <p class="text-xs sm:text-sm text-slate-600 leading-relaxed">
          国際的な医学データベース（PubMed・Cochrane等）で報告されている<strong>{e(ev_domain_label)}領域の代表的な査読済み鍼灸研究</strong>をまとめています（標準治療との併用や研究上の限界を含めて正確に掲載）。
        </p>
        <div class="overflow-x-auto rounded-xl border border-slate-200">
          <table class="w-full min-w-[560px] text-xs sm:text-sm text-left border-collapse">
            <thead class="bg-[#16284c] text-white">
              <tr><th class="p-3">年・研究デザイン</th><th class="p-3">対象・規模</th><th class="p-3">主な結果</th><th class="p-3">限界・出典</th></tr>
            </thead>
            <tbody class="divide-y divide-slate-200 text-slate-700 bg-white">{paper_rows}</tbody>
          </table>
        </div>
        <div>
          <p class="text-xs font-bold text-[#16284c] mb-1.5">参考文献・出典</p>
          <ol class="list-decimal pl-5 text-xs text-slate-600 space-y-1">{"".join(ref_items)}</ol>
        </div>
      </div>
    </details>

    <div class="rounded-2xl bg-[#fffbeb] border-l-4 border-[#b68d40] px-4 py-3.5 text-xs sm:text-sm text-slate-700 leading-relaxed">
      <strong class="text-[#8c6239]">⚠ 受診を最優先すべきサイン：</strong>{e(ev["red_flags"])}
    </div>
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




SUMMARIES_DIR = DISEASE_DIR / "summaries"


def _wrap_jp(text: str, width: int = 24, max_lines: int = 3) -> list[str]:
    clean = re.sub(r"\s+", " ", (text or "").replace("※", " ※")).strip()
    if not clean:
        return []
    lines = []
    cur = ""
    for ch in clean:
        cur += ch
        if len(cur) >= width:
            lines.append(cur)
            cur = ""
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(clean) > sum(len(l) for l in lines) and lines:
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def _short_bullet(text: str, max_chars: int = 14) -> str:
    clean = re.sub(r"^[・•\-*0-9.０-９．、\s]+", "", (text or "").strip())
    clean = re.sub(r"\s+", " ", clean)
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1] + "…"


def _split_clauses(text: str, limit: int = 3, max_chars: int = 14) -> list[str]:
    clean = re.sub(r"\s+", " ", (text or "").replace("※", "。")).strip()
    parts = [p.strip() for p in re.split(r"[。、/／・\n]+", clean) if len(p.strip()) >= 4]
    out = []
    for p in parts:
        b = _short_bullet(p, max_chars=max_chars)
        if b and b not in out:
            out.append(b)
        if len(out) >= limit:
            break
    return out


def build_page_summary_svg(d: dict) -> str:
    """パターンA：3ステップ矢印フロー型（文字はみ出し完全ゼロ設計）の要点まとめ図解SVGを生成する。"""
    e = html.escape
    accent, gold, tint, dark = CATEGORY_PALETTES.get(d["category"], ("#16284c", "#b68d40", "#f6f4ef", "#233d70"))
    title_str = d["name"]
    if len(title_str) > 16:
        title_text = title_str[:15] + "…の整理と施術方針"
    else:
        title_text = f"{title_str}の整理と施術方針"
    if len(title_text) > 24:
        title_size = 26
    elif len(title_text) > 19:
        title_size = 30
    else:
        title_size = 34

    sym_items = [_short_bullet(s, 14) for s in split_items(d.get("symptoms", ""))[:3]]
    if not sym_items:
        sym_items = ["気になるつらい症状がある", "日常や睡眠に支障が出ている", "体質から整えたい"]
    mech_items = _split_clauses(d.get("mechanism", ""), limit=3, max_chars=14)
    if not mech_items:
        mech_items = ["神経・血流のバランス乱れ", "深層筋の防御性こわばり", "自律神経の過緊張が持続"]
    app_items = _split_clauses(d.get("approach", ""), limit=3, max_chars=14)
    if not app_items:
        app_items = ["過敏な神経の興奮を鎮静", "深層筋と微小循環を整える", "標準治療と安全に併用可能"]

    def _render_bullets(items: list[str], x: int, color: str) -> str:
        return "".join(
            f'<text x="{x}" y="{362 + i * 38}" font-family="Noto Sans JP, sans-serif" font-size="16" fill="{color}">・{e(item)}</text>'
            for i, item in enumerate(items[:3])
        )

    sym_svg = _render_bullets(sym_items, 66, "#334155")
    mech_svg = _render_bullets(mech_items, 454, "#334155")
    app_svg = _render_bullets(app_items, 842, "#f1f5f9")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 620" width="1200" height="620" role="img" aria-label="{e(d['name'])}の3ステップ要点図解">
  <defs>
    <linearGradient id="hdrA" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{accent}"/>
      <stop offset="100%" stop-color="{dark}"/>
    </linearGradient>
    <clipPath id="clip1"><rect x="52" y="330" width="320" height="140"/></clipPath>
    <clipPath id="clip2"><rect x="440" y="330" width="320" height="140"/></clipPath>
    <clipPath id="clip3"><rect x="828" y="330" width="320" height="140"/></clipPath>
  </defs>
  <rect width="1200" height="620" rx="24" fill="#faf8f4"/>
  <rect width="1200" height="148" rx="24" fill="url(#hdrA)"/>
  <rect y="120" width="1200" height="28" fill="url(#hdrA)"/>
  <rect x="48" y="28" width="248" height="28" rx="14" fill="{gold}"/>
  <text x="172" y="47" text-anchor="middle" font-family="Noto Sans JP, sans-serif" font-size="13" font-weight="700" fill="#ffffff">{e(d['category'])}｜3ステップ図解</text>
  <text x="1152" y="47" text-anchor="end" font-family="Noto Serif JP, serif" font-size="14" fill="#cbd5e1">銀座そうぜん鍼灸院グループ</text>
  <text x="48" y="106" font-family="Noto Serif JP, serif" font-size="{title_size}" font-weight="700" fill="#ffffff">{e(title_text)}</text>

  <rect x="44" y="182" width="336" height="366" rx="20" fill="#ffffff" stroke="#e2dcd0" stroke-width="2"/>
  <circle cx="212" cy="242" r="34" fill="#faf5eb" stroke="{gold}" stroke-width="2"/>
  <text x="212" y="251" text-anchor="middle" font-family="Noto Serif JP, serif" font-size="22" font-weight="700" fill="{gold}">01</text>
  <text x="212" y="304" text-anchor="middle" font-family="Noto Serif JP, serif" font-size="20" font-weight="700" fill="{accent}">よくあるサイン</text>
  <line x1="84" y1="322" x2="340" y2="322" stroke="#efeae0" stroke-width="2"/>
  <g clip-path="url(#clip1)">{sym_svg}</g>
  <rect x="72" y="478" width="280" height="44" rx="12" fill="{tint}"/>
  <text x="212" y="505" text-anchor="middle" font-family="Noto Sans JP, sans-serif" font-size="14" font-weight="700" fill="{accent}">まず原因部位を丁寧に見極め</text>

  <polygon points="392,365 416,350 416,380" fill="{gold}" transform="rotate(180 404 365)"/>

  <rect x="432" y="182" width="336" height="366" rx="20" fill="#ffffff" stroke="#e2dcd0" stroke-width="2"/>
  <circle cx="600" cy="242" r="34" fill="{tint}" stroke="{accent}" stroke-width="2"/>
  <text x="600" y="251" text-anchor="middle" font-family="Noto Serif JP, serif" font-size="22" font-weight="700" fill="{accent}">02</text>
  <text x="600" y="304" text-anchor="middle" font-family="Noto Serif JP, serif" font-size="20" font-weight="700" fill="{accent}">体の中で起きていること</text>
  <line x1="472" y1="322" x2="728" y2="322" stroke="#efeae0" stroke-width="2"/>
  <g clip-path="url(#clip2)">{mech_svg}</g>
  <rect x="460" y="478" width="280" height="44" rx="12" fill="#faf5eb"/>
  <text x="600" y="505" text-anchor="middle" font-family="Noto Sans JP, sans-serif" font-size="14" font-weight="700" fill="#8c6239">神経・血流・筋緊張から整理</text>

  <polygon points="780,365 804,350 804,380" fill="{gold}" transform="rotate(180 792 365)"/>

  <rect x="820" y="182" width="336" height="366" rx="20" fill="{accent}"/>
  <circle cx="988" cy="242" r="34" fill="{gold}"/>
  <text x="988" y="251" text-anchor="middle" font-family="Noto Serif JP, serif" font-size="22" font-weight="700" fill="#ffffff">03</text>
  <text x="988" y="304" text-anchor="middle" font-family="Noto Serif JP, serif" font-size="20" font-weight="700" fill="#ffffff">当院の鍼灸アプローチ</text>
  <line x1="860" y1="322" x2="1116" y2="322" stroke="#ffffff33" stroke-width="2"/>
  <g clip-path="url(#clip3)">{app_svg}</g>
  <rect x="848" y="478" width="280" height="44" rx="12" fill="{gold}"/>
  <text x="988" y="505" text-anchor="middle" font-family="Noto Sans JP, sans-serif" font-size="14" font-weight="700" fill="#ffffff">段階的に回復と安定を目指す</text>

  <text x="600" y="592" text-anchor="middle" font-family="Noto Sans JP, sans-serif" font-size="13" fill="#64748b">※標準治療・専門医の診察と併用し、お一人おひとりの体質と経過に合わせて施術内容を調整します</text>
</svg>"""


def render_page_summary_figure(d: dict) -> str:
    e = html.escape
    return (
        f'<figure class="rounded-2xl border border-[#e5dfd3] bg-[#faf8f4] p-2.5 sm:p-3">'
        f'<img src="./summaries/{e(d["slug"])}-summary.svg" alt="{e(d["name"])}の要点まとめ図解" loading="lazy" class="w-full h-auto rounded-xl" />'
        f'<figcaption class="text-[11px] sm:text-xs text-slate-500 mt-2 text-center">図：{e(d["name"])}の要点まとめ（症状・原因・当院の施術方針）</figcaption>'
        f'</figure>'
    )


def strip_legacy_stock_images(html_text: str) -> str:
    """旧サイト(s-treatment.com)やimages/youtube/由来のフリー素材imgタグと旧s-treatment.comリンクを除外する。"""
    def _repl_img(m: re.Match) -> str:
        tag = m.group(0)
        if "s-treatment.com" in tag or "images/youtube/" in tag:
            return ""
        return tag
    out = re.sub(r"<img\b[^>]*>", _repl_img, html_text, flags=re.IGNORECASE)
    out = re.sub(r'<a\b[^>]*href=["\']https?://[^"\']*s-treatment\.com[^"\']*["\'][^>]*>(.*?)</a>', r"\1", out, flags=re.IGNORECASE | re.DOTALL)
    return out


def ensure_portal_three_way_nav(total_diseases: int) -> None:
    """トップページ(index.html)へ三者動線（鍼灸院↔臨床改善事例↔1,200疾患解説）と?tab=cases自動切替を安全に組み込む。"""
    root_index = BASE_DIR / "index.html"
    if not root_index.exists():
        return
    html_text = root_index.read_text(encoding="utf-8")
    marker = "data-sozen-threeway-nav"
    snippet = f"""<script {marker}="v1">
(function(){{
  function initThreeWayNav(){{
    var params = new URLSearchParams(window.location.search);
    var wantCases = params.get('tab') === 'cases' || window.location.hash === '#cases';
    var btns = Array.from(document.querySelectorAll('button, a'));
    var caseBtn = btns.find(function(el){{ return /改善事例/.test(el.textContent || ''); }});
    if (wantCases && caseBtn) {{
      setTimeout(function(){{ caseBtn.click(); }}, 60);
    }}
    if (!document.getElementById('sozen-disease-portal-link') && caseBtn && caseBtn.parentElement) {{
      var a = document.createElement('a');
      a.id = 'sozen-disease-portal-link';
      a.href = './disease/index.html';
      a.textContent = '🌿 症状・疾患解説（{total_diseases:,}件）から探す →';
      a.style.cssText = 'display:block;width:100%;max-width:460px;margin:10px auto 0;padding:12px 20px;border-radius:9999px;background:#faf5eb;border:1.5px solid #d4b886;color:#16284c;font-weight:700;font-size:14px;text-align:center;text-decoration:none;box-shadow:0 2px 8px rgba(22,40,76,0.06);';
      caseBtn.parentElement.insertAdjacentElement('afterend', a);
    }}
  }}
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initThreeWayNav);
  else initThreeWayNav();
}})();
</script>"""
    if marker in html_text:
        html_text = re.sub(r'<script[^>]*data-sozen-threeway-nav[^>]*>.*?</script>', snippet, html_text, flags=re.DOTALL)
    elif "</body>" in html_text:
        html_text = html_text.replace("</body>", snippet + "\n</body>", 1)
    root_index.write_text(html_text, encoding="utf-8")


def render_detail_html(d: dict, by_category: dict[str, list[dict]]) -> str:
    e = html.escape
    symptoms = split_items(d["symptoms"])
    approaches = split_items(d["approach"])
    faqs = parse_faq(d["faq"])
    tags = [t.strip() for t in d["keywords"].split(",") if t.strip()][:6]
    canonical_url = f"{SITE_BASE_URL}/disease/{d['slug']}.html"
    og_image = f"{SITE_BASE_URL}/images/diseases/{d['image_file']}" if d["image_exists"] else ""

    rich_path = ARTICLES_DIR / f"{d['slug']}.html"
    rich_html = strip_legacy_stock_images(fold_tables_in_rich_html(rich_path.read_text(encoding="utf-8"))) if rich_path.exists() else ""
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

    # よくある質問（案B：アコーディオンでスッキリ収納）
    faq_card_html = ""
    if faqs:
        faq_items = []
        for f in faqs:
            faq_items.append(
                f'<div class="rounded-xl border border-slate-200 bg-[#faf8f4] p-4 space-y-1.5">'
                f'<p class="text-xs sm:text-sm font-bold text-[#16284c]">Q. {e(f["q"])}</p>'
                f'<p class="text-xs sm:text-sm text-slate-600 leading-relaxed">{e(f["a"])}</p>'
                f'</div>'
            )
        faq_card_html = (
            f'<details id="sec-faq" class="bg-white rounded-2xl border border-[#e8e3d9] shadow-sm overflow-hidden">'
            f'<summary class="px-5 py-4 flex items-center justify-between gap-3 cursor-pointer hover:bg-[#faf8f4] transition-colors">'
            f'<div><p class="text-[11px] font-bold text-[#b68d40]">06. よくある質問（FAQ）</p>'
            f'<h2 class="font-mincho text-sm sm:text-base font-bold text-[#16284c]">よくあるご質問を見る（{len(faqs)}件）</h2></div>'
            f'<span class="chev text-slate-400 shrink-0">▼</span></summary>'
            f'<div class="px-5 pb-5 pt-3 border-t border-[#ece6da] space-y-2.5">{"".join(faq_items)}</div>'
            f'</details>'
        )

    tags_html = "".join(f'<span class="px-3 py-1 rounded-full bg-white border border-[#e8e3d9] text-slate-600 text-xs">#{e(t)}</span>' for t in tags)
    related_html = render_related_html(pick_related_diseases(d, by_category, limit=6), d["category"])

    visual_hero_card = render_plan_b_visual_card(d)
    mech_block_html = render_mechanism_block(d)
    if rich_html:
        rich_with_mech = inject_mechanism_into_rich_html(rich_html, mech_block_html)
        content_cards = (
            f'{visual_hero_card}'
            f'{yt_card_html}'
            f'<details id="sec-acupoints" class="bg-white rounded-2xl border border-[#e8e3d9] shadow-sm overflow-hidden" open>'
            f'<summary class="px-5 py-4 flex items-center justify-between gap-3 cursor-pointer hover:bg-[#faf8f4] transition-colors">'
            f'<div><p class="text-[11px] font-bold text-[#b68d40]">詳細解説</p>'
            f'<h2 class="font-mincho text-sm sm:text-base font-bold text-[#16284c]">{e(d["name"])}の詳しい医学解説・施術方針</h2></div>'
            f'<span class="chev text-slate-400 shrink-0">▼</span></summary>'
            f'<div class="px-5 sm:px-7 pb-6 pt-4 border-t border-[#ece6da] rich-article">{rich_with_mech}</div>'
            f'</details>'
        )
    else:
        src_link = (
            f'（<a href="{e(d["source_url"])}" target="_blank" rel="noopener" class="text-[#16284c] underline">出典文献</a>）'
            if d.get("source_url") and d["source_url"].startswith("http") and "s-treatment.com" not in d["source_url"].lower() else ""
        )
        sym_lis = "".join(
            f'<li class="flex items-start gap-2 text-slate-700 text-sm"><span class="text-[#b68d40] font-bold">✓</span><span>{e(s)}</span></li>'
            for s in symptoms
        )
        app_cards = "".join(
            f'<div class="rounded-2xl bg-[#faf8f4] border border-[#ece6da] p-4 text-sm text-slate-700 leading-relaxed">{e(a)}</div>'
            for a in approaches
        )
        acc_mech = (
            f'<details id="sec-mechanism" class="bg-white rounded-2xl border border-[#e8e3d9] shadow-sm overflow-hidden">'
            f'<summary class="px-5 py-4 flex items-center justify-between gap-3 cursor-pointer hover:bg-[#faf8f4] transition-colors">'
            f'<div><p class="text-[11px] font-bold text-[#b68d40]">01. 原因とメカニズム</p>'
            f'<h2 class="font-mincho text-sm sm:text-base font-bold text-[#16284c]">なぜ症状が起きるのか・鍼灸の作用機序を読む</h2></div>'
            f'<span class="chev text-slate-400 shrink-0">▼</span></summary>'
            f'<div class="px-5 pb-5 pt-4 border-t border-[#ece6da] space-y-4">'
            f'<p class="text-sm sm:text-[15px] text-slate-700 leading-[1.85]">{e(d["mechanism"])}{src_link}</p>'
            f'{mech_block_html}'
            f'</div></details>'
        )
        acc_sym = (
            f'<details class="bg-white rounded-2xl border border-[#e8e3d9] shadow-sm overflow-hidden">'
            f'<summary class="px-5 py-4 flex items-center justify-between gap-3 cursor-pointer hover:bg-[#faf8f4] transition-colors">'
            f'<div><p class="text-[11px] font-bold text-[#b68d40]">02. よくあるお悩み</p>'
            f'<h2 class="font-mincho text-sm sm:text-base font-bold text-[#16284c]">このような症状はありませんか？（{len(symptoms)}項目）</h2></div>'
            f'<span class="chev text-slate-400 shrink-0">▼</span></summary>'
            f'<div class="px-5 pb-5 pt-4 border-t border-[#ece6da]"><ul class="space-y-2">{sym_lis}</ul></div>'
            f'</details>'
            if sym_lis else ""
        )
        acc_app = (
            f'<details class="bg-white rounded-2xl border border-[#e8e3d9] shadow-sm overflow-hidden">'
            f'<summary class="px-5 py-4 flex items-center justify-between gap-3 cursor-pointer hover:bg-[#faf8f4] transition-colors">'
            f'<div><p class="text-[11px] font-bold text-[#b68d40]">03. 当院の施術アプローチ</p>'
            f'<h2 class="font-mincho text-sm sm:text-base font-bold text-[#16284c]">施術の進め方・改善のポイントを見る</h2></div>'
            f'<span class="chev text-slate-400 shrink-0">▼</span></summary>'
            f'<div class="px-5 pb-5 pt-4 border-t border-[#ece6da] space-y-2.5">{app_cards}</div>'
            f'</details>'
            if app_cards else ""
        )
        accordion_intro = '<p class="text-xs font-semibold text-slate-500 px-1 pt-1">▼ 詳しく知りたい項目だけタップして開けます（全項目の医学解説を収録）</p>'
        content_cards = (
            visual_hero_card
            + "\n"
            + yt_card_html
            + "\n<div class=\"space-y-2.5\">"
            + accordion_intro
            + acc_mech
            + acc_sym
            + acc_app
            + render_category_cards(d)
            + "</div>"
        )

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
  <div class="max-w-3xl mx-auto px-3.5 sm:px-5 py-5 sm:py-8 space-y-6">
    <div class="text-center">
      <div class="inline-flex flex-wrap justify-center items-center gap-1.5 bg-white p-1.5 rounded-2xl sm:rounded-full border border-[#e8e3d9] shadow-sm">
        <a href="{e(PORTAL_URL)}" class="px-3 sm:px-4 py-1.5 sm:py-2 rounded-full text-xs sm:text-sm font-medium text-slate-600 hover:bg-[#faf5eb] hover:text-[#16284c] transition-colors">🏥 鍼灸院から探す</a>
        <a href="{e(PORTAL_URL)}?tab=cases" class="px-3 sm:px-4 py-1.5 sm:py-2 rounded-full text-xs sm:text-sm font-semibold text-[#16284c] hover:bg-[#faf5eb] transition-colors">✨ 臨床改善事例</a>
        <a href="./index.html" class="px-3 sm:px-4 py-1.5 sm:py-2 rounded-full text-xs sm:text-sm font-semibold bg-[#16284c] text-white shadow-sm">🌿 症状から探す</a>
        <a href="../symptom-check.html" class="px-3 sm:px-4 py-1.5 sm:py-2 rounded-full text-xs sm:text-sm font-semibold text-[#8c6239] hover:bg-[#faf5eb] transition-colors">🧭 30秒ナビ</a>
      </div>
    </div>
    <nav class="text-xs text-slate-500 flex flex-wrap items-center gap-2">
      <a href="{e(PORTAL_URL)}" class="hover:text-[#16284c] underline">🏥 鍼灸院ポータル</a><span>/</span>
      <a href="{e(PORTAL_URL)}?tab=cases" class="hover:text-[#16284c] underline">✨ 臨床改善事例</a><span>/</span>
      <a href="./index.html" class="hover:text-[#16284c] underline">🌿 症状・疾患から探す</a><span>/</span>
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

    <section class="rounded-3xl bg-white border border-[#e8e3d9] shadow-sm p-6 sm:p-7 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
      <div class="space-y-1">
        <p class="text-[11px] font-bold tracking-wider text-[#b68d40]">CLINICAL RECOVERY CASES</p>
        <h2 class="font-mincho text-base sm:text-lg font-bold text-[#16284c]">✨ 実際の臨床改善事例・回復経過を見る</h2>
        <p class="text-xs sm:text-sm text-slate-600">銀座本院をはじめ各院での実際の改善事例を症状別に絞り込んでご確認いただけます。</p>
      </div>
      <a href="{e(PORTAL_URL)}?tab=cases" class="shrink-0 px-5 py-2.5 rounded-full bg-[#faf5eb] border border-[#d4b886] hover:bg-[#b68d40] hover:text-white text-[#16284c] text-xs sm:text-sm font-bold transition-colors">改善事例一覧へ →</a>
    </section>

    <section class="rounded-3xl bg-[#faf5eb] border border-[#e5d5b5] p-6 sm:p-7 text-center space-y-3">
      <p class="font-mincho text-lg font-bold text-[#16284c]">おひとりで悩まず、まずはご相談ください</p>
      <p class="text-xs sm:text-sm text-slate-600">銀座本院・川口・大宮・横浜・博多・田無の各院でご予約を承っております。</p>
      <div class="flex flex-wrap justify-center gap-2.5 pt-2">
        <a href="{e(PORTAL_URL)}" class="px-6 py-3 rounded-full bg-[#16284c] hover:bg-[#243b6b] text-white text-sm font-semibold transition-colors">🏥 ご予約・全国各院を探す →</a>
        <a href="{e(PORTAL_URL)}?tab=cases" class="px-5 py-3 rounded-full bg-white border border-[#d4b886] hover:bg-[#faf5eb] text-[#16284c] text-sm font-semibold transition-colors">✨ 臨床改善事例を見る</a>
        <a href="./index.html" class="px-5 py-3 rounded-full bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 text-sm font-medium transition-colors">← 疾患一覧に戻る</a>
      </div>
    </section>
  </div>

  <div class="sm:hidden fixed bottom-0 inset-x-0 z-40 bg-white/95 backdrop-blur border-t border-slate-200 shadow-[0_-4px_16px_rgba(15,23,42,0.08)] px-3 py-2.5">
    <div class="max-w-lg mx-auto grid grid-cols-12 gap-1.5 items-center">
      <a href="{e(PORTAL_URL)}" class="col-span-4 py-2.5 px-2 rounded-full bg-[#16284c] text-white text-[11px] font-bold text-center shadow-sm">📅 ご予約</a>
      <a href="{e(PORTAL_URL)}?tab=cases" class="col-span-3 py-2.5 px-1.5 rounded-full bg-[#faf5eb] border border-[#d4b886] text-[#16284c] text-[11px] font-bold text-center">✨ 事例</a>
      <a href="../symptom-check.html" class="col-span-3 py-2.5 px-1.5 rounded-full bg-[#faf5eb] border border-[#e5d5b5] text-[#8c6239] text-[11px] font-bold text-center">🧭 ナビ</a>
      <a href="./index.html" class="col-span-2 py-2.5 px-1 rounded-full bg-slate-100 text-slate-700 text-[11px] font-semibold text-center">🔍 一覧</a>
    </div>
  </div>
  <script>
    document.querySelectorAll('a[href^="#sec-"]').forEach(function(a){{
      a.addEventListener('click', function(){{
        var target = document.querySelector(a.getAttribute('href'));
        if (target && target.tagName === 'DETAILS') target.open = true;
      }});
    }});
  </script>
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
  <meta name="description" content="銀座そうぜん鍼灸院グループが対応する全{len(diseases):,}疾患・症状をひらがな・キーワード・カテゴリから検索できる総合解説ページです。" />
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
      <div class="inline-flex flex-wrap justify-center items-center gap-1.5 sm:gap-2 bg-white p-1.5 rounded-2xl sm:rounded-full border border-[#e8e3d9] shadow-sm mt-5">
        <a href="{e(PORTAL_URL)}" class="px-3 sm:px-4 py-2 rounded-full text-xs sm:text-sm font-medium text-slate-600 hover:text-[#16284c]">🏥 鍼灸院から探す</a>
        <a href="{e(PORTAL_URL)}?tab=cases" class="px-3 sm:px-4 py-2 rounded-full text-xs sm:text-sm font-semibold text-[#16284c] hover:bg-[#faf5eb]">✨ 臨床改善事例</a>
        <span class="px-3 sm:px-4 py-2 rounded-full text-xs sm:text-sm font-medium bg-[#16284c] text-white">🌿 症状から探す</span>
        <a href="../symptom-check.html" class="px-3 sm:px-4 py-2 rounded-full text-xs sm:text-sm font-semibold text-[#8c6239] hover:bg-[#faf5eb]">🧭 30秒ナビ</a>
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
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    for d in diseases:
        (cards_dir / f"{d['slug']}.svg").write_text(generate_unique_card_svg(d), encoding="utf-8")
        (mech_dir / f"{d['slug']}.svg").write_text(generate_mechanism_svg(d), encoding="utf-8")
        # 案B移行により旧文字SVG生成をスキップ（実物イラスト2枚＋HTML3要約カードへ統合）
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