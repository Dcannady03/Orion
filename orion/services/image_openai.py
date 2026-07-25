"""OpenAI Images API adapter for Orion's provider-neutral Image Center."""
from __future__ import annotations

import base64
import binascii
import ipaddress
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from orion.services.image import (
    IMAGE_FORMATS,
    ImageGenerationRequest,
    ImageProviderError,
    ImageProviderOutput,
    ImageProviderStatus,
    ProviderImage,
    sniff_image,
)


class OpenAIImageAdapter:
    name = "openai"
    ALLOWED_SIZES = ("1024x1024", "1024x1536", "1536x1024", "auto")
    ALLOWED_QUALITIES = ("low", "medium", "high", "auto")
    ALLOWED_FORMATS = ("png", "jpeg", "webp")

    def __init__(
        self,
        config_manager,
        secret_store,
        *,
        client_factory: Callable[..., Any] | None = None,
        downloader: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config_manager
        self.secrets = secret_store
        self._client_factory = client_factory
        self._downloader = downloader or requests.get

    @property
    def model(self) -> str:
        return str(self.config.get("image.openai.model", "gpt-image-2")).strip()

    def _client(self):
        api_key = str(self.secrets.get("openai") or "").strip()
        if not api_key:
            raise ImageProviderError("credential_missing", "OpenAI image generation requires a configured OpenAI credential.")
        factory = self._client_factory
        if factory is None:
            try:
                from openai import OpenAI  # type: ignore
            except ImportError as exc:
                raise ImageProviderError("client_missing", "OpenAI image support requires the official openai package.") from exc
            factory = OpenAI
        timeout = max(1.0, min(float(self.config.get("image.request_timeout_seconds", 120)), 600.0))
        base_url = str(self.config.get("providers.openai.base_url", "https://api.openai.com/v1")).strip()
        return factory(api_key=api_key, base_url=base_url, timeout=timeout)

    def status(self) -> ImageProviderStatus:
        if not bool(self.config.get("image.enabled", True)):
            return ImageProviderStatus(self.name, "OpenAI", "disabled", "Image Center is disabled.", self.model)
        if not str(self.secrets.get("openai") or "").strip():
            return ImageProviderStatus(self.name, "OpenAI", "credential_missing", "OpenAI credential is missing.", self.model, self.ALLOWED_FORMATS, self.ALLOWED_SIZES)
        if self._client_factory is None:
            try:
                import openai  # type: ignore  # noqa: F401
            except ImportError:
                return ImageProviderStatus(self.name, "OpenAI", "client_missing", "Official openai package is not installed.", self.model, self.ALLOWED_FORMATS, self.ALLOWED_SIZES)
        return ImageProviderStatus(self.name, "OpenAI", "ready", "Configured locally; no API request was made.", self.model, self.ALLOWED_FORMATS, self.ALLOWED_SIZES, 1)

    def generate(self, request: ImageGenerationRequest) -> ImageProviderOutput:
        model = request.model or self.model
        if request.size not in self.ALLOWED_SIZES:
            raise ImageProviderError("invalid_request", "Requested image size is not allowed.")
        if request.quality not in self.ALLOWED_QUALITIES:
            raise ImageProviderError("invalid_request", "Requested image quality is not allowed.")
        if request.output_format not in self.ALLOWED_FORMATS:
            raise ImageProviderError("invalid_request", "Requested image format is not allowed.")
        if request.count != 1:
            raise ImageProviderError("invalid_request", "OpenAI Image Center currently generates one image per request.")
        try:
            response = self._client().images.generate(
                model=model,
                prompt=request.prompt,
                n=request.count,
                size=request.size,
                quality=request.quality,
                output_format="jpeg" if request.output_format == "jpg" else request.output_format,
            )
            rows = list(getattr(response, "data", ()) or ())
            if len(rows) != request.count:
                raise ImageProviderError("invalid_response", "OpenAI returned an unexpected image count.")
            images: list[ProviderImage] = []
            max_bytes = max(1, int(self.config.get("image.max_image_bytes", 20_971_520)))
            for row in rows:
                encoded = str(getattr(row, "b64_json", "") or "")
                url = str(getattr(row, "url", "") or "")
                if encoded:
                    if len(encoded) > ((max_bytes + 2) // 3) * 4 + 16:
                        raise ImageProviderError("response_too_large", "OpenAI image response exceeded the configured byte limit.")
                    try:
                        data = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise ImageProviderError("invalid_response", "OpenAI returned malformed encoded image data.") from exc
                elif url:
                    data = self._download(url, max_bytes)
                else:
                    raise ImageProviderError("invalid_response", "OpenAI returned no supported image payload.")
                if len(data) > max_bytes:
                    raise ImageProviderError("response_too_large", "OpenAI image exceeded the configured byte limit.")
                mime, _, _ = sniff_image(data)
                expected = IMAGE_FORMATS[request.output_format]
                if mime != expected:
                    raise ImageProviderError("invalid_response", "OpenAI image content did not match the requested format.")
                images.append(ProviderImage(data, mime, str(getattr(row, "revised_prompt", "") or "")[:1_000]))
            usage_value = getattr(response, "usage", None)
            usage = {}
            if usage_value is not None:
                for key in ("input_tokens", "output_tokens", "total_tokens"):
                    value = getattr(usage_value, key, None)
                    if isinstance(value, int) and value >= 0:
                        usage[key] = value
            return ImageProviderOutput(self.name, model, tuple(images), usage, None)
        except ImageProviderError:
            raise
        except Exception as exc:
            raise self._sanitized_error(exc) from exc

    def _download(self, url: str, maximum: int) -> bytes:
        try:
            parsed = urlsplit(url)
            hostname = (parsed.hostname or "").rstrip(".").casefold()
        except (ValueError, UnicodeError):
            hostname = ""
            parsed = urlsplit("")
        try:
            address = ipaddress.ip_address(hostname) if hostname else None
        except ValueError:
            address = None
        if (
            parsed.scheme.casefold() != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or hostname == "localhost"
            or hostname.endswith(".localhost")
            or hostname.endswith(".local")
            or address is not None and not address.is_global
        ):
            raise ImageProviderError("invalid_response", "OpenAI returned an unsafe image URL.")
        timeout = max(1.0, min(float(self.config.get("image.request_timeout_seconds", 120)), 600.0))
        try:
            response = self._downloader(url, timeout=timeout, stream=True, allow_redirects=False)
            response.raise_for_status()
            content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
            if content_type not in {"image/png", "image/jpeg", "image/webp"}:
                raise ImageProviderError("invalid_response", "OpenAI download returned an unexpected media type.")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > maximum:
                    raise ImageProviderError("response_too_large", "OpenAI download exceeded the configured byte limit.")
                chunks.append(chunk)
            data = b"".join(chunks)
            sniff_image(data)
            return data
        except ImageProviderError:
            raise
        except requests.Timeout as exc:
            raise ImageProviderError("timed_out", "OpenAI image download timed out.") from exc
        except requests.RequestException as exc:
            raise ImageProviderError("provider_unavailable", "OpenAI image download failed safely.") from exc

    @staticmethod
    def _sanitized_error(exc: Exception) -> ImageProviderError:
        name = type(exc).__name__.casefold()
        code = str(getattr(exc, "code", "") or "").casefold()
        status = getattr(exc, "status_code", None)
        if "timeout" in name:
            return ImageProviderError("timed_out", "OpenAI image generation timed out.")
        if status == 429 or "ratelimit" in name:
            return ImageProviderError("rate_limited", "OpenAI image generation is rate limited.")
        if code == "moderation_blocked" or "moderation" in code:
            return ImageProviderError("content_rejected", "OpenAI rejected the prompt under provider safety policy.")
        if status in {401, 403} or "authentication" in name or "permission" in name:
            return ImageProviderError("provider_unavailable", "OpenAI image credentials or access were rejected.")
        if status == 400 or "badrequest" in name:
            return ImageProviderError("invalid_request", "OpenAI rejected the image request.")
        return ImageProviderError("generation_failed", "OpenAI image generation failed safely.")
