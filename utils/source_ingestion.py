import io
import ipaddress
import os
import re
import socket
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
REQUEST_TIMEOUT_SECONDS = 25
ALLOWED_URL_SCHEMES = {"http", "https"}
ALLOWED_SOURCE_DOMAINS = {
    "youtube.com",
    "youtu.be",
}
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


def _is_hostname_allowed(hostname):
    host = (hostname or "").lower().strip(".")
    if not host:
        return False
    for allowed in ALLOWED_SOURCE_DOMAINS:
        allowed = allowed.lower()
        if host == allowed or host.endswith(f".{allowed}"):
            return True
    return False


def _is_forbidden_ip(ip_obj):
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def _ensure_hostname_not_private(hostname):
    host = (hostname or "").lower().strip(".")
    if not host:
        raise ValueError("Invalid URL: missing hostname.")
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("Invalid URL: localhost is not allowed.")

    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        ip_obj = None

    if ip_obj is not None:
        if _is_forbidden_ip(ip_obj):
            raise ValueError("Invalid URL: private or local IP addresses are not allowed.")
        return

    try:
        resolved = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError("Invalid URL: hostname could not be resolved.") from exc

    seen_ips = set()
    for item in resolved:
        sockaddr = item[4]
        if not sockaddr:
            continue
        ip_text = sockaddr[0]
        if ip_text in seen_ips:
            continue
        seen_ips.add(ip_text)
        ip_obj = ipaddress.ip_address(ip_text)
        if _is_forbidden_ip(ip_obj):
            raise ValueError("Invalid URL: resolved to a private or local IP address.")


def _validate_and_normalize_source_url(raw_url):
    url = (raw_url or "").strip()
    if not url:
        raise ValueError("Invalid URL: URL is empty.")

    parsed = urlparse(url)
    if not parsed.scheme:
        url = f"https://{url}"
        parsed = urlparse(url)

    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise ValueError("Invalid URL: only http and https schemes are allowed.")

    if not parsed.netloc:
        raise ValueError("Invalid URL: missing network location.")

    host = (parsed.hostname or "").lower().strip(".")
    if not _is_hostname_allowed(host):
        raise ValueError("Invalid URL: domain is not in the trusted allowlist.")

    _ensure_hostname_not_private(host)
    return parsed.geturl()


def _fetch_text_url(url, timeout=REQUEST_TIMEOUT_SECONDS):
    response = requests.get(url, timeout=timeout, headers=DEFAULT_HEADERS)
    response.raise_for_status()
    return response.text


def _normalize_url_input(raw_url):
    return _validate_and_normalize_source_url(raw_url)


def _extract_website_text(url):
    html = _fetch_text_url(url, timeout=REQUEST_TIMEOUT_SECONDS)
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
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"} and parsed.path.startswith("/shorts/"):
        return parsed.path.split("/shorts/")[-1].split("/")[0]
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
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
        response = requests.get(export_url, timeout=REQUEST_TIMEOUT_SECONDS, headers=DEFAULT_HEADERS)
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
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}:
        return _extract_youtube_transcript(url)
    if host == "docs.google.com" and ("/document/" in path or "/presentation/" in path):
        return _extract_google_doc_or_slide(url)
    if path.endswith(".pdf"):
        return _extract_pdf_text_from_url(url)
    raise ValueError("Invalid URL: unsupported source type for the trusted domains.")


def build_source_bundle(direct_text, source_urls, source_files):
    sections = []
    errors = []
    source_labels = []

    direct_text = (direct_text or "").strip()
    if direct_text:
        sections.append(f"[Manual Input]\n{direct_text}")
        source_labels.append("Manual Input")

    for raw_url in source_urls:
        if not (raw_url or "").strip():
            continue
        try:
            url = _normalize_url_input(raw_url)
            extracted = _extract_url_text(url)
            if extracted:
                sections.append(f"[Source URL: {url}]\n{extracted}")
                source_labels.append(url)
        except ValueError as exc:
            errors.append(f"URL failed ({raw_url}): {exc}")
        except requests.RequestException as exc:
            errors.append(f"URL failed ({raw_url}): request error ({exc})")
        except Exception as exc:
            errors.append(f"URL failed ({raw_url}): {exc}")

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
