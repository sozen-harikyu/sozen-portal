#!/usr/bin/env python3
"""
銀座そうぜん鍼灸院 ポータル用 YouTube切り抜き・動画リンク 厳選自動移植スクリプト（v2）
https://www.s-treatment.com/ の各疾患ページから、
「YouTube動画リンク」と「YouTube切り抜き画像（16:9横長比率 or 動画見出し配下）」のみを厳格に抽出し、
関係のない院内写真やバナーを除外して移植します。
"""

from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import html
import io
import os
import re
import shutil
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ARTICLES_DIR = BASE_DIR / "articles"
YOUTUBE_IMAGES_DIR = BASE_DIR / "images" / "youtube"
REPORT_PATH = BASE_DIR / "import_youtube_report.md"

SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "").strip()
TARGET_SLUGS_RAW = os.environ.get("TARGET_SLUGS", "zunari").strip()
OVERWRITE_EXISTING = os.environ.get("OVERWRITE_EXISTING", "false").lower() == "true"
EXTRACTION_MODE = os.environ.get("EXTRACTION_MODE", "youtube_and_clips").strip().lower()
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "4") or "4")
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.2") or "0.2")
MAX_CLIPS_PER_PAGE = int(os.environ.get("MAX_CLIPS_PER_PAGE", "6") or "6")
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://sozen-harikyu.github.io/sozen-portal").rstrip("/")

USER_AGENT = "Mozilla/5.0 (compatible; SozenPortalMigrator/2.0; +https://sozen-harikyu.github.io/sozen-portal/)"

EXCLUDED_IMG_PATTERNS = re.compile(
    r"(logo|header|footer|navi|nav_|menu|btn|button|banner|bnr|icon|arrow|bullet|"
    r"spacer|blank|pixel|line_?btn|yoyaku|reserve|tel_|phone|contact|access|map_|"
    r"instagram|twitter|facebook|tiktok|sns_|share_|print_|pagetop|totop|"
    r"common/|parts/|template/|favicon|apple-touch|loading|spinner|noimage|"
    r"badge|star|check_?mark|qa_|faq_?icon|staff|clinic|exterior|interior|room|bed|map)",
    re.IGNORECASE,
)

EXCLUDED_ALT_PATTERNS = re.compile(
    r"(ネット予約|LINE予約|電話予約|ご予約|お問い合わせ|アクセス|地図|ロゴ|"
    r"バナー|ボタン|トップへ|ホーム|メニュー|インスタグラム|公式LINE|QRコード|"
    r"院内|外観|スタッフ|受付|ベッド|料金表)",
    re.IGNORECASE,
)

YOUTUBE_CONTEXT_RE = re.compile(r"(youtube|ユーチューブ|動画|切り抜き|チャンネル|解説動画)", re.IGNORECASE)

BOILERPLATE_TEXT_PATTERNS = re.compile(
    r"^(ご予約はこちら|ネット予約はこちら|LINE予約はこちら|お電話でのご予約|"
    r"お問い合わせはこちら|トップページへ戻る|ページトップへ|プライバシーポリシー|"
    r"Copyright|©|ALL RIGHTS RESERVED|TEL\s*[:：]?\s*0\d{1,4}-\d{1,4}-\d{3,4}|"
    r"営業時間|休診日|アクセス方法|銀座院|赤坂院|新宿院|池袋院|川口院|博多院)$",
    re.IGNORECASE,
)

YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s\"'<>]*v=[a-zA-Z0-9_-]{11}[^\s\"'<>]*|"
    r"shorts/[a-zA-Z0-9_-]{11}[^\s\"'<>]*|embed/[a-zA-Z0-9_-]{11}[^\s\"'<>]*)|"
    r"youtu\.be/[a-zA-Z0-9_-]{11}[^\s\"'<>]*)",
    re.IGNORECASE,
)

YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?[^\"'\s]*v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
    re.IGNORECASE,
)

SKIP_CONTAINER_TAGS = {"script", "style", "noscript", "header", "footer", "nav", "aside", "form", "svg"}
SKIP_CLASS_ID_RE = re.compile(
    r"(^|[\s_-])(header|footer|gnav|global-nav|navi|nav|menu|sidebar|side|sub-?col|"
    r"widget|breadcrumb|pankuzu|pagetop|related|recommend|cta-?box|reserve-?box|"
    r"banner|ad-?area|sns-?area|share|copyright|clinic-?list|access-?box)([\s_-]|$)",
    re.IGNORECASE,
)
MAIN_CLASS_ID_RE = re.compile(
    r"(^|[\s_-])(main|contents?|entry-?content|post-?content|article-?body|"
    r"cms-?content|page-?body|block-?main|column-?main)([\s_-]|$)",
    re.IGNORECASE,
)


def fetch_url_bytes(url: str, referer: str = "", timeout: int = 25, retries: int = 2) -> tuple[bytes, str]:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ctype = resp.headers.get("Content-Type", "")
                return resp.read(), ctype
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Fetch failed for {url}: {last_err}")


def decode_html_bytes(data: bytes, content_type: str = "") -> str:
    m = re.search(r"charset=([^\s;]+)", content_type, re.IGNORECASE)
    encodings = []
    if m:
        encodings.append(m.group(1).strip("\"'"))
    head = data[:4096].decode("ascii", errors="ignore")
    m2 = re.search(r"charset=[\"']?([a-zA-Z0-9_-]+)", head, re.IGNORECASE)
    if m2:
        encodings.append(m2.group(1))
    encodings.extend(["utf-8", "cp932", "shift_jis", "euc-jp"])
    for enc in encodings:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def sanitize_slug(slug: str, fallback_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", (slug or "").strip())
    return cleaned or re.sub(r"[^a-zA-Z0-9_-]", "", fallback_id.lower())


def fetch_csv_text(source: str) -> str:
    if os.path.exists(source):
        return Path(source).read_text(encoding="utf-8-sig")
    data, _ = fetch_url_bytes(source, timeout=30)
    return data.decode("utf-8-sig", errors="replace")


def load_diseases(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    rows: list[dict] = []
    col_map: dict[str, str] = {}
    for col in (reader.fieldnames or []):
        c = col.strip()
        if c == "ID":
            col_map["id"] = col
        elif "疾患名" in c:
            col_map["name"] = col
        elif "slug" in c.lower():
            col_map["slug"] = col
        elif "参考URL" in c or "元ページ" in c:
            col_map["source_url"] = col

    for row in reader:
        def get(k: str) -> str:
            return (row.get(col_map.get(k, ""), "") or "").strip()

        did = get("id")
        name = get("name")
        url = get("source_url")
        if not name or not url.startswith(("http://", "https://")):
            continue
        rows.append({
            "id": did,
            "name": name,
            "slug": sanitize_slug(get("slug"), did or name),
            "source_url": url,
        })
    return rows


def detect_image_info(data: bytes) -> tuple[str, int, int]:
    if len(data) < 24:
        return "", 0, 0
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return ".png", w, h
    if data[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", data[6:10])
        return ".gif", w, h
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return ".webp", w, h
        if chunk == b"VP8 " and len(data) >= 30:
            w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
            h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
            return ".webp", w, h
        if chunk == b"VP8L" and len(data) >= 25:
            b0, b1, b2, b3 = data[21:25]
            bits = b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return ".webp", w, h
        return ".webp", 0, 0
    if data[:2] == b"\xff\xd8":
        i = 2
        n = len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            while i < n and data[i] == 0xFF:
                i += 1
            if i >= n:
                break
            marker = data[i]
            i += 1
            if marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
                continue
            if i + 2 > n:
                break
            seg_len = struct.unpack(">H", data[i:i + 2])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                if i + 7 <= n:
                    h, w = struct.unpack(">HH", data[i + 3:i + 7])
                    return ".jpg", w, h
                break
            if seg_len < 2:
                break
            i += seg_len
        return ".jpg", 0, 0
    return "", 0, 0


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def extract_youtube_id(url: str) -> str:
    m = YOUTUBE_ID_RE.search(url or "")
    return m.group(1) if m else ""


class AkibareContentParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.blocks: list[dict] = []
        self.skip_depth = 0
        self.in_main_depth = 0
        self.saw_main_container = False

        self.active_tag: str | None = None
        self.active_buf: list[str] = []
        self.in_list_depth = 0
        self.current_list_items: list[str] = []
        self.in_li = False
        self.li_buf: list[str] = []
        self.current_a_href = ""

    def _is_skipped_attr(self, attrs_dict: dict[str, str]) -> bool:
        cls = attrs_dict.get("class", "")
        el_id = attrs_dict.get("id", "")
        role = attrs_dict.get("role", "")
        if role in ("navigation", "banner", "contentinfo", "complementary"):
            return True
        combined = f"{cls} {el_id}"
        if combined.strip() and SKIP_CLASS_ID_RE.search(combined):
            if not MAIN_CLASS_ID_RE.search(combined):
                return True
        return False

    def _is_main_attr(self, tag: str, attrs_dict: dict[str, str]) -> bool:
        if tag in ("main", "article"):
            return True
        if attrs_dict.get("role", "") == "main":
            return True
        combined = f"{attrs_dict.get('class', '')} {attrs_dict.get('id', '')}"
        return bool(combined.strip() and MAIN_CLASS_ID_RE.search(combined))

    def _flush_active(self) -> None:
        if not self.active_tag:
            return
        text = clean_text("".join(self.active_buf))
        tag = self.active_tag
        self.active_tag = None
        self.active_buf = []
        if not text or BOILERPLATE_TEXT_PATTERNS.match(text):
            return
        if tag in ("h2", "h3", "h4"):
            self.blocks.append({"type": "heading", "level": tag, "text": text, "in_main": self.in_main_depth > 0})
        elif tag == "p":
            self.blocks.append({"type": "paragraph", "text": text, "in_main": self.in_main_depth > 0})

    def _flush_list(self) -> None:
        items = [it for it in self.current_list_items if it and not BOILERPLATE_TEXT_PATTERNS.match(it)]
        self.current_list_items = []
        if items:
            self.blocks.append({"type": "list", "items": items, "in_main": self.in_main_depth > 0})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}

        if self.skip_depth > 0:
            if tag not in ("img", "br", "hr", "meta", "link", "input"):
                self.skip_depth += 1
            return

        if tag in SKIP_CONTAINER_TAGS or self._is_skipped_attr(attrs_dict):
            if tag not in ("img", "br", "hr", "meta", "link", "input"):
                self.skip_depth = 1
            return

        if self._is_main_attr(tag, attrs_dict):
            self.saw_main_container = True
            self.in_main_depth += 1
        elif self.in_main_depth > 0 and tag not in ("img", "br", "hr", "meta", "link", "input"):
            self.in_main_depth += 1

        if tag == "iframe":
            src = attrs_dict.get("src", "") or attrs_dict.get("data-src", "")
            vid = extract_youtube_id(src)
            if vid:
                self._flush_active()
                self.blocks.append({
                    "type": "youtube",
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "video_id": vid,
                    "in_main": self.in_main_depth > 0,
                })
            return

        if tag == "a":
            href = attrs_dict.get("href", "")
            self.current_a_href = href
            vid = extract_youtube_id(href)
            if vid:
                self._flush_active()
                self.blocks.append({
                    "type": "youtube",
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "video_id": vid,
                    "in_main": self.in_main_depth > 0,
                })
            return

        if tag == "img":
            src = (
                attrs_dict.get("data-src")
                or attrs_dict.get("data-original")
                or attrs_dict.get("src")
                or ""
            ).strip()
            if src and not src.startswith("data:"):
                abs_url = urllib.parse.urljoin(self.page_url, src)
                alt = clean_text(attrs_dict.get("alt", ""))
                self._flush_active()
                self.blocks.append({
                    "type": "image",
                    "url": abs_url,
                    "alt": alt,
                    "link_href": self.current_a_href,
                    "in_main": self.in_main_depth > 0,
                })
            return

        if tag in ("h2", "h3", "h4"):
            self._flush_active()
            self._flush_list()
            self.active_tag = tag
            self.active_buf = []
        elif tag in ("ul", "ol"):
            self._flush_active()
            self.in_list_depth += 1
        elif tag == "li" and self.in_list_depth > 0:
            self.in_li = True
            self.li_buf = []
        elif tag == "p" and not self.in_li:
            self._flush_active()
            self.active_tag = "p"
            self.active_buf = []
        elif tag == "br":
            if self.in_li:
                self.li_buf.append(" ")
            elif self.active_tag:
                self.active_buf.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth > 0:
            if tag not in ("img", "br", "hr", "meta", "link", "input"):
                self.skip_depth -= 1
            return

        if tag == "a":
            self.current_a_href = ""
        elif tag in ("h2", "h3", "h4", "p") and self.active_tag == tag:
            self._flush_active()
        elif tag == "li" and self.in_li:
            item = clean_text("".join(self.li_buf))
            if item:
                self.current_list_items.append(item)
            self.in_li = False
            self.li_buf = []
        elif tag in ("ul", "ol") and self.in_list_depth > 0:
            self.in_list_depth -= 1
            if self.in_list_depth == 0:
                self._flush_list()

        if self.in_main_depth > 0 and tag not in ("img", "br", "hr", "meta", "link", "input"):
            self.in_main_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth > 0:
            return
        if self.in_li:
            self.li_buf.append(data)
        elif self.active_tag:
            self.active_buf.append(data)
        else:
            for m in YOUTUBE_URL_RE.finditer(data):
                vid = extract_youtube_id(m.group(0))
                if vid:
                    self.blocks.append({
                        "type": "youtube",
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "video_id": vid,
                        "in_main": self.in_main_depth > 0,
                    })


def is_candidate_image_url(url: str, alt: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    if EXCLUDED_IMG_PATTERNS.search(url):
        return False
    if alt and EXCLUDED_ALT_PATTERNS.search(alt):
        return False
    lower = url.lower().split("?")[0]
    if lower.endswith((".svg", ".ico")):
        return False
    return True


def fetch_and_parse_page(disease: dict) -> dict:
    url = disease["source_url"]
    if REQUEST_DELAY > 0:
        time.sleep(REQUEST_DELAY)
    try:
        raw_bytes, ctype = fetch_url_bytes(url, referer="https://www.s-treatment.com/")
        html_text = decode_html_bytes(raw_bytes, ctype)
    except Exception as exc:
        return {"disease": disease, "error": str(exc), "blocks": []}

    parser = AkibareContentParser(url)
    parser.feed(html_text)
    parser.close()

    blocks = parser.blocks
    if parser.saw_main_container:
        main_blocks = [b for b in blocks if b.get("in_main")]
        if main_blocks:
            blocks = main_blocks

    filtered: list[dict] = []
    for b in blocks:
        if b["type"] == "image":
            if is_candidate_image_url(b["url"], b.get("alt", "")):
                filtered.append(b)
        else:
            filtered.append(b)

    return {"disease": disease, "error": "", "blocks": filtered}


def build_sections(blocks: list[dict], shared_img_urls: set[str]) -> list[dict]:
    sections: list[dict] = []
    current = {"heading": "", "heading_level": "h2", "blocks": [], "has_youtube": False, "has_image": False}

    for b in blocks:
        if b["type"] == "image" and b["url"] in shared_img_urls:
            continue
        if b["type"] == "heading":
            if current["heading"] or current["blocks"]:
                sections.append(current)
            current = {
                "heading": b["text"],
                "heading_level": b.get("level", "h2"),
                "blocks": [],
                "has_youtube": bool(YOUTUBE_CONTEXT_RE.search(b["text"])),
                "has_image": False,
            }
            continue
        if b["type"] == "youtube":
            current["has_youtube"] = True
        elif b["type"] == "image":
            current["has_image"] = True
            if YOUTUBE_CONTEXT_RE.search(b.get("alt", "")) or extract_youtube_id(b.get("link_href", "")):
                current["has_youtube"] = True
        current["blocks"].append(b)

    if current["heading"] or current["blocks"]:
        sections.append(current)

    return sections


def download_and_validate_youtube_clip(
    url: str,
    page_url: str,
    dest_dir: Path,
    index: int,
    strict_16_9: bool,
) -> tuple[str, int, int] | None:
    """YouTube切り抜き画像のみを厳選して保存（16:9横長比率・320px以上を厳格チェック）。"""
    try:
        data, _ = fetch_url_bytes(url, referer=page_url, timeout=20, retries=2)
    except Exception:
        return None
    if len(data) < 8000:
        return None
    ext, w, h = detect_image_info(data)
    if not ext or w < 320 or h < 180:
        return None
    ratio = w / max(h, 1)
    if strict_16_9:
        # YouTube動画の切り抜き（16:9 ≒ 1.77）に近い横長画像のみ採用（縦長写真・正方形アイコン・細長バナーを除外）
        if not (1.50 <= ratio <= 2.02):
            return None
    else:
        if not (1.20 <= ratio <= 2.40):
            return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"clip_{index:02d}{ext}"
    (dest_dir / filename).write_bytes(data)
    return filename, w, h


def generate_youtube_markdown(
    disease: dict,
    sections: list[dict],
    mode: str,
) -> tuple[str, int, int]:
    slug = disease["slug"]
    page_url = disease["source_url"]
    img_dir = YOUTUBE_IMAGES_DIR / slug

    # 上書きモード時は古い非YouTube画像を掃除してから再保存
    if OVERWRITE_EXISTING and img_dir.exists():
        shutil.rmtree(img_dir)

    selected_sections = (
        sections if mode == "all_sections_with_media"
        else [s for s in sections if s["has_youtube"] or s["has_image"]]
    )
    if not selected_sections:
        return "", 0, 0

    md_lines: list[str] = [
        f"<!-- Auto-imported (YouTube-filtered v2) from {page_url} on {datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M JST')} -->",
        "",
    ]

    seen_video_ids: set[str] = set()
    seen_img_hashes: set[str] = set()
    saved_images = 0
    saved_videos = 0

    for sec in selected_sections:
        sec_lines: list[str] = []
        sec_has_actual_media = False
        strict_16_9 = not sec["has_youtube"]

        if sec["heading"]:
            prefix = "### " if sec["heading_level"] == "h3" else "## "
            sec_lines.append(f"{prefix}{sec['heading']}")
            sec_lines.append("")

        for b in sec["blocks"]:
            btype = b["type"]
            if btype == "youtube":
                vid = b["video_id"]
                if vid and vid not in seen_video_ids:
                    seen_video_ids.add(vid)
                    sec_lines.append(f"[YouTube: https://www.youtube.com/watch?v={vid}]")
                    sec_lines.append("")
                    saved_videos += 1
                    sec_has_actual_media = True
            elif btype == "image":
                if saved_images >= MAX_CLIPS_PER_PAGE:
                    continue
                img_url = b["url"]
                url_hash = hashlib.md5(img_url.encode("utf-8")).hexdigest()
                if url_hash in seen_img_hashes:
                    continue
                result = download_and_validate_youtube_clip(
                    img_url, page_url, img_dir, saved_images + 1, strict_16_9=strict_16_9
                )
                if result:
                    fname, _, _ = result
                    seen_img_hashes.add(url_hash)
                    saved_images += 1
                    sec_has_actual_media = True
                    alt = b.get("alt", "").replace("|", " ").strip()
                    if alt:
                        sec_lines.append(f"[画像: {fname} | {alt}]")
                    else:
                        sec_lines.append(f"[画像: {fname}]")
                    sec_lines.append("")
            elif btype == "list":
                sec_lines.append("[ポイント]")
                for item in b["items"]:
                    sec_lines.append(f"・{item}")
                sec_lines.append("[/ポイント]")
                sec_lines.append("")
            elif btype == "paragraph":
                text = b["text"].strip()
                if len(text) >= 4:
                    sec_lines.append(text)
                    sec_lines.append("")

        if sec_has_actual_media:
            md_lines.extend(sec_lines)

    if saved_images == 0 and saved_videos == 0:
        return "", 0, 0

    return "\n".join(md_lines).strip() + "\n", saved_images, saved_videos


def write_report(
    total_scanned: int,
    migrated: list[dict],
    skipped_existing: list[str],
    skipped_no_media: int,
    errors: list[dict],
    shared_img_count: int,
) -> None:
    jst = timezone(timedelta(hours=9))
    now_str = datetime.now(jst).strftime("%Y-%m-%d %H:%M JST")
    lines = [
        "# YouTube切り抜き・動画リンク 厳選自動移植レポート（v2）",
        "",
        f"- 実行日時: {now_str}",
        f"- 対象指定: `{TARGET_SLUGS_RAW}`",
        f"- 走査ページ数: {total_scanned} ページ",
        f"- 移植完了ページ数: **{len(migrated)} ページ**",
        f"- 既存ファイル保護スキップ: {len(skipped_existing)} ページ",
        f"- YouTube動画・16:9切り抜きなしスキップ: {skipped_no_media} ページ",
        f"- サイト共通画像 自動除外URL数: {shared_img_count} 件",
        f"- 取得エラー: {len(errors)} 件",
        "",
    ]
    if migrated:
        lines.append("## 移植完了ページ一覧")
        lines.append("")
        lines.append("| ID | 疾患名 | slug | 切り抜き画像 | YouTube動画 | 元ページ | ポータルURL |")
        lines.append("|---|---|---|---:|---:|---|---|")
        for m in migrated:
            portal_url = f"{SITE_BASE_URL}/disease/{m['slug']}.html"
            lines.append(
                f"| {m['id']} | {m['name']} | `{m['slug']}` | {m['images']} | {m['videos']} | "
                f"[元ページ]({m['source_url']}) | [確認]({portal_url}) |"
            )
        lines.append("")
    if errors:
        lines.append("## 取得エラー一覧")
        lines.append("")
        for err in errors[:50]:
            lines.append(f"- `{err['slug']}` ({err['name']}): {err['error']}")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not SHEET_CSV_URL:
        print("ERROR: SHEET_CSV_URL environment variable is required.", file=sys.stderr)
        return 1

    print("Fetching disease master CSV...")
    csv_text = fetch_csv_text(SHEET_CSV_URL)
    diseases = load_diseases(csv_text)
    print(f"Loaded {len(diseases)} diseases with valid s-treatment.com URLs.")

    if TARGET_SLUGS_RAW.upper() != "ALL":
        targets = {s.strip().lower() for s in re.split(r"[,\s]+", TARGET_SLUGS_RAW) if s.strip()}
        diseases = [d for d in diseases if d["slug"].lower() in targets or d["id"].lower() in targets]
        print(f"Filtered to {len(diseases)} target page(s): {[d['slug'] for d in diseases]}")

    if not diseases:
        print("No matching diseases found. Exiting.")
        return 0

    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    YOUTUBE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    to_fetch: list[dict] = []
    skipped_existing: list[str] = []
    for d in diseases:
        md_path = ARTICLES_DIR / f"{d['slug']}_youtube.md"
        if md_path.exists() and not OVERWRITE_EXISTING:
            skipped_existing.append(d["slug"])
        else:
            to_fetch.append(d)

    print(f"Scanning {len(to_fetch)} page(s) (skipped existing: {len(skipped_existing)})...")

    parsed_results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(fetch_and_parse_page, d) for d in to_fetch]
        for idx, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            parsed_results.append(fut.result())
            if idx % 50 == 0 or idx == len(to_fetch):
                print(f"  Fetched {idx}/{len(to_fetch)} pages...")

    img_page_counter: Counter[str] = Counter()
    for res in parsed_results:
        unique_urls = {b["url"] for b in res["blocks"] if b["type"] == "image"}
        for u in unique_urls:
            img_page_counter[u] += 1

    shared_threshold = 4 if len(parsed_results) >= 12 else 999999
    shared_img_urls = {u for u, cnt in img_page_counter.items() if cnt >= shared_threshold}

    migrated: list[dict] = []
    errors: list[dict] = []
    skipped_no_media = 0

    parsed_results.sort(key=lambda r: r["disease"]["id"])
    for res in parsed_results:
        d = res["disease"]
        if res["error"]:
            errors.append({"slug": d["slug"], "name": d["name"], "error": res["error"]})
            continue

        sections = build_sections(res["blocks"], shared_img_urls)
        md_text, img_cnt, vid_cnt = generate_youtube_markdown(d, sections, EXTRACTION_MODE)
        if not md_text:
            skipped_no_media += 1
            continue

        md_path = ARTICLES_DIR / f"{d['slug']}_youtube.md"
        md_path.write_text(md_text, encoding="utf-8")
        migrated.append({
            "id": d["id"],
            "name": d["name"],
            "slug": d["slug"],
            "images": img_cnt,
            "videos": vid_cnt,
            "source_url": d["source_url"],
        })
        print(f"  [OK] {d['id']} {d['name']} ({d['slug']}): {img_cnt} clips, {vid_cnt} videos")

    write_report(
        total_scanned=len(to_fetch),
        migrated=migrated,
        skipped_existing=skipped_existing,
        skipped_no_media=skipped_no_media,
        errors=errors,
        shared_img_count=len(shared_img_urls),
    )
    print(f"Done! Migrated {len(migrated)} page(s). Report saved to {REPORT_PATH.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
