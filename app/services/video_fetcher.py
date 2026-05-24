import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from app.utils.logger import get_logger

logger = get_logger(__name__)

PEXELS_API_BASE = "https://api.pexels.com"

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "he",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "that",
    "the",
    "to",
    "was",
    "were",
    "will",
    "with",
    "you",
    "your",
    "we",
    "our",
    "they",
    "their",
    "this",
    "those",
    "these",
    "or",
    "if",
    "then",
    "than",
    "about",
    "into",
    "while",
    "during",
    "after",
    "before",
    "over",
    "under",
    "up",
    "down",
    "out",
    "so",
    "such",
}


def _split_sentences(script: str) -> List[str]:
    parts = re.split(r"[\n\.\!\?;:]+", script)
    return [p.strip() for p in parts if p.strip()]


def _extract_keywords(text: str, max_keywords: int = 5) -> List[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z\-']+", text.lower())
    scored: Dict[str, int] = {}
    for word in words:
        cleaned = word.strip("-'")
        if len(cleaned) <= 2 or cleaned in _STOPWORDS:
            continue
        scored[cleaned] = scored.get(cleaned, 0) + 1

    ranked = sorted(scored.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    return [word for word, _ in ranked[:max_keywords]]


def _semantic_score(query_keywords: Sequence[str], haystack: str) -> float:
    haystack_tokens = set(re.findall(r"[a-zA-Z][a-zA-Z\-']+", haystack.lower()))
    if not haystack_tokens:
        return 0.0

    matches = 0.0
    for keyword in query_keywords:
        if keyword in haystack_tokens:
            matches += 1.0
        elif any(keyword in token or token in keyword for token in haystack_tokens):
            matches += 0.4

    return matches / max(len(query_keywords), 1)


def _pexels_request(path_with_query: str, api_key: str) -> Dict[str, object]:
    request = Request(
        f"{PEXELS_API_BASE}{path_with_query}",
        headers={"Authorization": api_key, "User-Agent": "video-automation/1.0"},
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_file(url: str, destination_dir: Path, prefix: str, extension: str) -> str:
    destination_dir.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=40) as response:
        with tempfile.NamedTemporaryFile(delete=False, dir=destination_dir, prefix=f"{prefix}_", suffix=extension) as out_file:
            out_file.write(response.read())
            return str(Path(out_file.name).resolve())


def _pick_best_video_file(video_files: Sequence[Dict[str, object]]) -> str:
    def _score(file_data: Dict[str, object]) -> Tuple[int, int, int]:
        width = int(file_data.get("width") or 0)
        height = int(file_data.get("height") or 0)
        area = width * height
        quality_bonus = 1 if str(file_data.get("quality", "")).lower() in {"hd", "fhd", "uhd"} else 0
        ratio_bonus = 1 if width >= height else 0
        return (quality_bonus, ratio_bonus, area)

    valid_files = [item for item in video_files if item.get("link")]
    if not valid_files:
        raise ValueError("Video result did not include downloadable files.")

    return str(max(valid_files, key=_score)["link"])


def fetch_assets(script: str, download_dir: str) -> List[str]:
    """Fetch stock assets from Pexels based on script sentences and return local file paths."""
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing PEXELS_API_KEY environment variable.")

    sentences = _split_sentences(script)
    if not sentences:
        return []

    downloaded_paths: List[str] = []

    for idx, sentence in enumerate(sentences, start=1):
        keywords = _extract_keywords(sentence)
        if not keywords:
            keywords = _extract_keywords(script)
        if not keywords:
            keywords = ["business"]

        query = " ".join(keywords[:3])
        encoded_query = quote_plus(query)
        logger.info("Searching Pexels for sentence=%s query='%s'", idx, query)

        try:
            payload = _pexels_request(f"/videos/search?query={encoded_query}&per_page=10&page=1", api_key=api_key)
            videos = payload.get("videos", []) if isinstance(payload, dict) else []
        except (HTTPError, URLError, TimeoutError) as exc:
            logger.warning("Pexels video search failed for query '%s': %s", query, exc)
            videos = []

        best_video_url = ""
        best_video_score = 0.0

        for video in videos:
            text_blob = " ".join(
                [
                    str(video.get("url", "")),
                    str(video.get("id", "")),
                    str(video.get("user", {}).get("name", "")) if isinstance(video.get("user"), dict) else "",
                ]
            )
            score = _semantic_score(keywords, text_blob)
            if score > best_video_score:
                try:
                    best_video_url = _pick_best_video_file(video.get("video_files", []))
                    best_video_score = score
                except ValueError:
                    continue

        if best_video_url and best_video_score >= 0.2:
            local_video = _download_file(
                best_video_url,
                destination_dir=Path(download_dir),
                prefix=f"clip_{idx:02d}",
                extension=".mp4",
            )
            downloaded_paths.append(local_video)
            continue

        logger.info("No suitable video found for query='%s'. Falling back to image.", query)
        try:
            image_payload = _pexels_request(f"/v1/search?query={encoded_query}&per_page=10&page=1", api_key=api_key)
            photos = image_payload.get("photos", []) if isinstance(image_payload, dict) else []
        except (HTTPError, URLError, TimeoutError) as exc:
            logger.warning("Pexels image search failed for query '%s': %s", query, exc)
            photos = []

        best_photo_url = ""
        best_photo_score = 0.0
        for photo in photos:
            src = photo.get("src", {}) if isinstance(photo, dict) else {}
            candidate = str(src.get("large2x") or src.get("large") or src.get("original") or "")
            text_blob = " ".join(
                [
                    str(photo.get("url", "")),
                    str(photo.get("photographer", "")),
                    str(photo.get("alt", "")),
                ]
            )
            score = _semantic_score(keywords, text_blob)
            if candidate and score > best_photo_score:
                best_photo_url = candidate
                best_photo_score = score

        if best_photo_url:
            local_image = _download_file(
                best_photo_url,
                destination_dir=Path(download_dir),
                prefix=f"image_{idx:02d}",
                extension=".jpg",
            )
            downloaded_paths.append(local_image)
        else:
            logger.warning("No image fallback found for sentence=%s query='%s'", idx, query)

    return downloaded_paths


class VideoFetcherService:
    """Fetches stock/raw video assets based on topic keywords."""

    def __init__(self) -> None:
        self.provider = os.getenv("VIDEO_PROVIDER", "pexels")

    def fetch(self, topic: str, download_dir: str, limit: int = 3) -> Dict[str, List[str]]:
        logger.info("Fetching videos with provider=%s, topic=%s", self.provider, topic)
        assets = fetch_assets(topic, download_dir=download_dir)[:limit]
        return {"provider": self.provider, "assets": assets}
