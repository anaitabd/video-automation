import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Optional

from openai import OpenAI


TimestampLevel = Literal["word", "sentence"]


@dataclass
class TimedToken:
    text: str
    start: float
    end: float


def _format_srt_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text


def _split_for_vertical_caption(text: str, max_chars_per_line: int = 28, max_lines: int = 2) -> str:
    words = text.split()
    if not words:
        return ""

    lines: List[str] = []
    current: List[str] = []

    for word in words:
        tentative = " ".join(current + [word])
        if len(tentative) <= max_chars_per_line:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]

    if current:
        lines.append(" ".join(current))

    if len(lines) <= max_lines:
        return "\n".join(lines)

    merged: List[str] = []
    chunk_size = (len(lines) + max_lines - 1) // max_lines
    for i in range(0, len(lines), chunk_size):
        merged.append(" ".join(lines[i : i + chunk_size]))
    return "\n".join(merged[:max_lines])


def _sentence_chunks_from_words(words: List[TimedToken], max_words_per_caption: int = 12) -> List[TimedToken]:
    chunks: List[TimedToken] = []
    current_words: List[TimedToken] = []

    def flush() -> None:
        if not current_words:
            return
        text = _clean_text(" ".join(w.text for w in current_words))
        chunks.append(TimedToken(text=text, start=current_words[0].start, end=current_words[-1].end))
        current_words.clear()

    for w in words:
        current_words.append(w)
        if len(current_words) >= max_words_per_caption:
            flush()
            continue
        if re.search(r"[.!?]$", w.text):
            flush()

    flush()
    return chunks


def _build_srt_entries(tokens: List[TimedToken], for_vertical_video: bool) -> List[str]:
    entries: List[str] = []
    for idx, token in enumerate(tokens, start=1):
        text = _clean_text(token.text)
        if for_vertical_video:
            text = _split_for_vertical_caption(text)
        if not text:
            continue
        entries.append(
            "\n".join(
                [
                    str(idx),
                    f"{_format_srt_timestamp(token.start)} --> {_format_srt_timestamp(token.end)}",
                    text,
                ]
            )
        )
    return entries


def generate_subtitles(
    audio_path: str,
    output_path: str,
    timestamp_level: TimestampLevel = "sentence",
    for_vertical_video: bool = True,
    model: str = "gpt-4o-transcribe",
) -> str:
    """
    Generate an SRT subtitle file from an audio file using OpenAI transcription.

    Args:
        audio_path: Path to source audio/video file.
        output_path: Destination .srt path.
        timestamp_level: "word" for per-word captions, "sentence" for grouped captions.
        for_vertical_video: Enables compact multi-line splitting for 9:16 videos.
        model: OpenAI speech-to-text model.

    Returns:
        Absolute path to generated .srt file.
    """
    source = Path(audio_path)
    if not source.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if timestamp_level not in {"word", "sentence"}:
        raise ValueError("timestamp_level must be either 'word' or 'sentence'")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    with source.open("rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model=model,
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    payload = json.loads(transcription.model_dump_json())
    words_data = payload.get("words") or []

    if not words_data:
        text = _clean_text(payload.get("text", ""))
        if not text:
            raise RuntimeError("Transcription succeeded but returned no text.")
        tokens = [TimedToken(text=text, start=0.0, end=2.0)]
    else:
        tokens = [
            TimedToken(text=w["word"], start=float(w["start"]), end=float(w["end"])) for w in words_data
        ]

    if timestamp_level == "word":
        caption_tokens = tokens
    else:
        caption_tokens = _sentence_chunks_from_words(tokens)

    srt_entries = _build_srt_entries(caption_tokens, for_vertical_video=for_vertical_video)
    srt_content = "\n\n".join(srt_entries) + "\n"

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(srt_content, encoding="utf-8")
    return str(out.resolve())
