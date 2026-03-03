import io
import os
import re
import tempfile
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from pypdf import PdfReader

from config import Config

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except Exception:  # pragma: no cover - optional dependency fallback
    YouTubeTranscriptApi = None


client = OpenAI(
    api_key=Config.GROQ_API_KEY,
    base_url=Config.GROQ_BASE_URL
)

MAX_SOURCE_CHARS = 120_000
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _normalize_whitespace(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate(text, limit=MAX_SOURCE_CHARS):
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[Truncated for length]"


def _fetch_text_url(url, timeout=25):
    response = requests.get(url, timeout=timeout, headers=DEFAULT_HEADERS)
    response.raise_for_status()
    return response.text


def _normalize_url_input(raw_url):
    url = (raw_url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme:
        url = f"https://{url}"
    return url


def _extract_website_text(url):
    html = _fetch_text_url(url, timeout=25)
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    normalized = _truncate(_normalize_whitespace(text))
    if len(normalized) >= 220:
        return normalized

    # Fallback for pages that block normal scraping.
    proxy_url = f"https://r.jina.ai/http://{url.replace('https://', '').replace('http://', '')}"
    proxy_text = _fetch_text_url(proxy_url, timeout=30)
    return _truncate(_normalize_whitespace(proxy_text))


def _extract_text_from_pdf_bytes(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    chunks = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    return _truncate("\n".join(chunks).strip())


def _extract_pdf_text_from_url(url):
    response = requests.get(url, timeout=30, headers=DEFAULT_HEADERS)
    response.raise_for_status()
    return _extract_text_from_pdf_bytes(response.content)


def _youtube_video_id(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/")
    if "youtube.com" in host and parsed.path.startswith("/shorts/"):
        return parsed.path.split("/shorts/")[-1].split("/")[0]
    if "youtube.com" in host:
        return parse_qs(parsed.query).get("v", [None])[0]
    return None


def _extract_youtube_transcript(url):
    video_id = _youtube_video_id(url)
    if not video_id:
        raise ValueError("Invalid YouTube URL.")
    if YouTubeTranscriptApi is not None:
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id)
            text = " ".join(item.get("text", "") for item in transcript)
            cleaned = _truncate(_normalize_whitespace(text))
            if cleaned:
                return cleaned
        except Exception:
            pass

    # Fallback path when transcript API is unavailable/blocked.
    return _extract_website_text(url)


def _extract_google_doc_or_slide(url):
    parsed = urlparse(url)
    path = parsed.path or ""
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", path)
    if not match:
        return _extract_website_text(url)

    file_id = match.group(1)
    if "docs.google.com/document/" in url:
        export_url = f"https://docs.google.com/document/d/{file_id}/export?format=txt"
        response = requests.get(export_url, timeout=25, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        return _truncate(_normalize_whitespace(response.text))

    if "docs.google.com/presentation/" in url:
        export_url = f"https://docs.google.com/presentation/d/{file_id}/export/pdf"
        response = requests.get(export_url, timeout=30, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        return _extract_text_from_pdf_bytes(response.content)

    return _extract_website_text(url)


def _extract_audio_transcript(file_storage):
    suffix = os.path.splitext(file_storage.filename or "")[1] or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as temp_file:
        file_storage.save(temp_file.name)
        with open(temp_file.name, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model=os.getenv("AUDIO_MODEL", "whisper-large-v3"),
                file=audio_file
            )
    return _truncate(_normalize_whitespace(getattr(transcript, "text", "")))


def _extract_uploaded_text(file_storage):
    filename = (file_storage.filename or "").lower()
    ext = os.path.splitext(filename)[1]

    if ext == ".pdf":
        return _extract_text_from_pdf_bytes(file_storage.read())

    if ext in {".txt", ".md", ".csv"}:
        raw = file_storage.read().decode("utf-8", errors="ignore")
        return _truncate(_normalize_whitespace(raw))

    if ext in {".mp3", ".wav", ".m4a", ".flac", ".ogg"}:
        file_storage.stream.seek(0)
        return _extract_audio_transcript(file_storage)

    raise ValueError(f"Unsupported file type: {ext or 'unknown'}")


def _extract_url_text(url):
    lower_url = url.lower()
    if "youtube.com" in lower_url or "youtu.be" in lower_url:
        return _extract_youtube_transcript(url)
    if "docs.google.com/document/" in lower_url or "docs.google.com/presentation/" in lower_url:
        return _extract_google_doc_or_slide(url)
    if lower_url.endswith(".pdf"):
        return _extract_pdf_text_from_url(url)
    return _extract_website_text(url)


def build_source_bundle(direct_text, source_urls, source_files):
    sections = []
    errors = []
    source_labels = []

    direct_text = (direct_text or "").strip()
    if direct_text:
        sections.append(f"[Manual Input]\n{direct_text}")
        source_labels.append("Manual Input")

    for raw_url in source_urls:
        url = _normalize_url_input(raw_url)
        if not url:
            continue
        try:
            extracted = _extract_url_text(url)
            if extracted:
                sections.append(f"[Source URL: {url}]\n{extracted}")
                source_labels.append(url)
        except Exception as exc:
            errors.append(f"URL failed ({url}): {exc}")

    for uploaded in source_files:
        if not uploaded or not uploaded.filename:
            continue
        try:
            uploaded.stream.seek(0)
            extracted = _extract_uploaded_text(uploaded)
            if extracted:
                sections.append(f"[Uploaded File: {uploaded.filename}]\n{extracted}")
                source_labels.append(uploaded.filename)
        except Exception as exc:
            errors.append(f"File failed ({uploaded.filename}): {exc}")

    merged = "\n\n".join(sections).strip()
    merged = _truncate(merged, limit=MAX_SOURCE_CHARS)
    return merged, source_labels, errors
