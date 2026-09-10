from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from devbrief.domain.contracts import TranscriptFixture, TranscriptSegment


class MediaError(RuntimeError):
    """A redacted ASR/TTS provider failure."""


class OpenAICompatibleMediaClient:
    """Minimal ASR and TTS client for OpenAI-compatible providers such as Bailian."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        asr_model: str = "qwen3-asr-flash",
        tts_model: str = "qwen3-tts-flash",
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("DEVBRIEF_BAILIAN_API_KEY")
        self.asr_model = asr_model
        self.tts_model = tts_model
        self.timeout = timeout

    def transcribe(
        self, path: Path, *, fixture_id: str | None = None
    ) -> TranscriptFixture:
        if not path.is_file():
            raise MediaError("audio file does not exist")
        payload = self._multipart_audio(path)
        try:
            result = self._request(
                "/audio/transcriptions",
                payload,
                content_type=f"multipart/form-data; boundary={payload[0]}",
            )
        except MediaError:
            result = self._transcribe_chat(path)
        text = result.get("text")
        if not isinstance(text, str) or not text.strip():
            result = self._transcribe_chat(path)
            text = result.get("text")
        if not isinstance(text, str) or not text.strip():
            raise MediaError("ASR response did not contain transcript text")
        segments = segments_from_response(result, text)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        return TranscriptFixture(
            fixture_id=fixture_id or f"audio-{digest}",
            fixture_version="1.0.0",
            redacted=True,
            segments=segments,
        )

    def _transcribe_chat(self, path: Path) -> dict[str, object]:
        mime = mimetypes.guess_type(path.name)[0] or "audio/wav"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        body = json.dumps(
            {
                "model": self.asr_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": f"data:{mime};base64,{encoded}"
                                },
                            }
                        ],
                    }
                ],
            }
        ).encode("utf-8")
        response = self._request_bytes("/chat/completions", body, "application/json")
        try:
            decoded: Any = json.loads(response.decode("utf-8"))
            message = decoded["choices"][0]["message"]["content"]
        except (
            KeyError,
            IndexError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise MediaError(
                "ASR chat response did not contain transcript text"
            ) from exc
        if isinstance(message, list):
            message_items = cast(list[Any], message)
            message = " ".join(_content_text(item) for item in message_items)
        if not isinstance(message, str):
            raise MediaError("ASR chat response did not contain transcript text")
        return {"text": message}

    def synthesize(self, text: str, output: Path, *, voice: str = "Cherry") -> Path:
        if not text.strip():
            raise MediaError("TTS text is required")
        body = json.dumps(
            {
                "model": self.tts_model,
                "input": text,
                "voice": voice,
                "response_format": "mp3",
            }
        ).encode("utf-8")
        try:
            audio = self._request_bytes("/audio/speech", body, "application/json")
        except MediaError:
            audio = self._synthesize_native(text, voice)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(audio)
        return output

    def _synthesize_native(self, text: str, voice: str) -> bytes:
        native_base = self.base_url.split("/compatible-mode/v1", maxsplit=1)[0]
        body = json.dumps(
            {
                "model": self.tts_model,
                "input": {"text": text, "voice": voice, "language_type": "Chinese"},
            }
        ).encode("utf-8")
        response = self._request_bytes(
            "/api/v1/services/aigc/multimodal-generation/generation",
            body,
            "application/json",
            base_url=native_base,
        )
        try:
            decoded: Any = json.loads(response.decode("utf-8"))
            audio_url = decoded["output"]["audio"]["url"]
        except (
            KeyError,
            IndexError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise MediaError("TTS response did not contain audio URL") from exc
        if not isinstance(audio_url, str) or not audio_url.startswith(
            ("http://", "https://")
        ):
            raise MediaError("TTS response contained an invalid audio URL")
        try:
            with urlopen(audio_url, timeout=self.timeout) as audio_response:
                return audio_response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise MediaError("TTS audio download failed") from exc

    def _multipart_audio(self, path: Path) -> tuple[str, bytes]:
        boundary = f"----devbrief-{uuid.uuid4().hex}"
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        content = path.read_bytes()
        fields = [
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n'
                f"{self.asr_model}\r\n"
            ).encode(),
            (
                f"--{boundary}\r\nContent-Disposition: form-data; "
                'name="response_format"\r\n\r\njson\r\n'
            ).encode(),
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n'
            ).encode()
            + content
            + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        return boundary, b"".join(fields)

    def _request(
        self, endpoint: str, payload: tuple[str, bytes], *, content_type: str
    ) -> dict[str, object]:
        body = self._request_bytes(endpoint, payload[1], content_type)
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MediaError("provider returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise MediaError("provider returned an invalid response")
        return cast(dict[str, object], result)

    def _request_bytes(
        self,
        endpoint: str,
        body: bytes,
        content_type: str,
        *,
        base_url: str | None = None,
    ) -> bytes:
        if not self.api_key:
            raise MediaError("DEVBRIEF_BAILIAN_API_KEY is required")
        request = Request(
            f"{base_url or self.base_url}{endpoint}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": content_type,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise MediaError("media provider request failed") from exc


def segments_from_response(
    result: Mapping[str, object], fallback_text: str
) -> list[TranscriptSegment]:
    raw_segments_value: Any = result.get("segments")
    if isinstance(raw_segments_value, list):
        raw_segments = cast(list[object], raw_segments_value)
        parsed: list[TranscriptSegment] = []
        for index, raw_value in enumerate(raw_segments):
            raw = (
                cast(dict[str, object], raw_value)
                if isinstance(raw_value, dict)
                else {}
            )
            text = raw.get("text")
            start = raw.get("start", 0)
            end = raw.get("end", 1)
            if (
                isinstance(text, str)
                and text.strip()
                and isinstance(start, (int, float))
                and isinstance(end, (int, float))
            ):
                parsed.append(
                    TranscriptSegment(
                        segment_id=f"seg-{index + 1}",
                        start_ms=max(0, int(start * 1000)),
                        end_ms=max(1, int(end * 1000)),
                        speaker="unknown",
                        text=_redact_text(text),
                    )
                )
        if parsed:
            return parsed
    return [
        TranscriptSegment(
            segment_id="seg-1",
            start_ms=0,
            end_ms=1000,
            speaker="unknown",
            text=_redact_text(fallback_text),
        )
    ]


def _content_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(cast(dict[str, Any], value).get("text", ""))


def _redact_text(value: str) -> str:
    value = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]", value
    )
    value = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,})\b", "[REDACTED]", value
    )
    value = re.sub(
        r"(?i)((?:token|password|secret)\s*[=:：]\s*)[^\s,;]+", r"\1[REDACTED]", value
    )
    return value[:2000]
