import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import requests

from orion.actions import ActionHistory, ActionService, PolicyDecision
from orion.core.router import CommandRouter
from orion.interfaces.discord import (
    DiscordBotInterface,
    DiscordImageIntentDetector,
    DiscordImagePolicy,
)
from orion.services.image import (
    IMAGE_SCHEMA_VERSION,
    ImageArtifact,
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageProviderError,
    ImageProviderOutput,
    ImageProviderRegistry,
    ImageProviderStatus,
    ImageService,
    ImageStore,
    ProviderImage,
)
from orion.services.image_openai import OpenAIImageAdapter


def png(width=2, height=3, extra=b"fixture"):
    return (
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" +
        int(width).to_bytes(4, "big") + int(height).to_bytes(4, "big") +
        b"\x08\x06\x00\x00\x00" + extra
    )


class Config:
    def __init__(self, values=None):
        self.values = {
            "image.enabled": True,
            "image.provider": "fake",
            "image.history_limit": 3,
            "image.history_display_limit": 10,
            "image.max_prompt_chars": 100,
            "image.prompt_preview_chars": 40,
            "image.max_images_per_request": 1,
            "image.max_image_bytes": 1024 * 1024,
            "image.request_timeout_seconds": 5,
            "image.max_concurrent_jobs": 2,
            "image.output_format": "png",
            "image.openai.model": "gpt-image-2",
            "image.openai.size": "1024x1024",
            "image.openai.quality": "medium",
            "providers.openai.base_url": "https://api.openai.com/v1",
            **(values or {}),
        }
        self.saved = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        self.saved += 1


class Secrets:
    def __init__(self, value="secret-key"):
        self.value = value

    def get(self, provider):
        return self.value if provider == "openai" else ""


class FakeAdapter:
    def __init__(self, name="fake", state="ready", error=None, data=None):
        self.name = name
        self.state = state
        self.error = error
        self.data = data or png()
        self.calls = []

    def status(self):
        return ImageProviderStatus(
            self.name, self.name.title(), self.state, f"{self.state} locally", "image-model",
            ("png",), ("1024x1024",), 1,
        )

    def generate(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return ImageProviderOutput(
            self.name, "image-model", (ProviderImage(self.data, "image/png", "safe revised"),),
            {"images": 1}, 0.01,
        )


class ServiceFixture:
    def make(self, root, *, adapter=None, config=None, registry=None):
        root = Path(root)
        workspace = root / "workspace"
        workspace.mkdir()
        selected = config or Config()
        selected_registry = registry or ImageProviderRegistry()
        adapter = adapter or FakeAdapter()
        if not selected_registry.statuses():
            selected_registry.register(adapter)
        counter = iter(range(1, 100))
        service = ImageService(
            selected,
            selected_registry,
            ImageStore(root / "user/images", history_limit=selected.get("image.history_limit", 3)),
            workspace,
            request_id_factory=lambda: f"request-{next(counter):012d}",
            image_id_factory=lambda: f"image-{next(counter):012d}",
            now=lambda: "2026-07-20T12:00:00+00:00",
        )
        return service, workspace, adapter, selected


class ImageSchemaTests(unittest.TestCase):
    def request_value(self):
        return {
            "schema_version": IMAGE_SCHEMA_VERSION,
            "request_id": "request-000000000001",
            "prompt": "A friendly robot",
            "count": 1,
            "size": "1024x1024",
            "quality": "medium",
            "output_format": "png",
            "source_interface": "cli",
            "created_at": "2026-07-20T12:00:00+00:00",
        }

    def test_request_is_strict_bounded_and_optional_fields_are_tolerant(self):
        request = ImageGenerationRequest.from_value(self.request_value(), max_prompt_chars=100)
        self.assertEqual(request.prompt, "A friendly robot")
        self.assertEqual(request.provider, "")
        unknown = self.request_value() | {"secret": "no"}
        with self.assertRaises(ValueError):
            ImageGenerationRequest.from_value(unknown)
        oversized = self.request_value() | {"prompt": "x" * 101}
        with self.assertRaises(ValueError):
            ImageGenerationRequest.from_value(oversized, max_prompt_chars=100)

    def test_artifact_rejects_invalid_mime_path_and_hash(self):
        value = {
            "artifact_id": "artifact-000000000001", "image_id": "image-000000000001",
            "path": "2026/07/image-000000000001/image-001.png", "filename": "image-001.png",
            "mime_type": "image/png", "byte_size": 10, "sha256": "a" * 64,
            "created_at": "2026-07-20T12:00:00+00:00", "provider": "fake", "model": "model",
        }
        self.assertIsNone(ImageArtifact.from_value(value).width)
        for change in (
            {"mime_type": "text/html"}, {"path": "../vault/key"}, {"sha256": "bad"},
            {"filename": "other.png"},
        ):
            with self.assertRaises(ValueError):
                ImageArtifact.from_value(value | change)

    def test_result_rejects_invalid_status_and_redacts_scalar_usage(self):
        value = {
            "schema_version": 1, "image_id": "image-000000000001",
            "request_id": "request-000000000001", "status": "failed",
            "requested_provider": "openai", "resolved_provider": "", "artifacts": [],
            "source_interface": "cli", "created_at": "2026-07-20T12:00:00+00:00",
            "completed_at": "2026-07-20T12:00:01+00:00", "duration_seconds": 1,
            "safe_error_category": "failed", "usage": {"note": "api_key=secret-value"},
        }
        result = ImageGenerationResult.from_value(value)
        self.assertNotIn("secret-value", json.dumps(result.to_dict()))
        with self.assertRaises(ValueError):
            ImageGenerationResult.from_value(value | {"status": "unknown"})
        with self.assertRaises(ValueError):
            ImageGenerationResult.from_value(value | {"sha256": "bad"})
        with self.assertRaises(ValueError):
            ImageGenerationResult.from_value(value | {"mime_type": "text/html"})


class ImageServiceStoreTests(unittest.TestCase, ServiceFixture):
    def test_generation_persists_external_artifact_hash_dimensions_and_no_base64(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, workspace, adapter, _ = self.make(tmp)
            result = service.generate("A friendly robot", source_interface="cli")
            self.assertEqual(result.status, "succeeded")
            artifact = result.artifacts[0]
            self.assertEqual((artifact.width, artifact.height), (2, 3))
            self.assertEqual(service.store.artifact_path(artifact).read_bytes(), png())
            self.assertNotIn(str(workspace), artifact.path)
            payload = (service.store.artifact_path(artifact).parent / "result.json").read_text(encoding="utf-8")
            self.assertNotIn(base64.b64encode(png()).decode(), payload)
            self.assertNotIn("secret", payload)
            self.assertEqual(adapter.calls[0].source_interface, "cli")
            self.assertEqual(result.image_count, 1)
            self.assertFalse(tuple(service.store.root.rglob("*.tmp")))

    def test_failed_attempt_is_persisted_with_safe_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            error = ImageProviderError("rate_limited", "Safe rate limit")
            service, _, _, _ = self.make(tmp, adapter=FakeAdapter(error=error))
            result = service.generate("A robot")
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.safe_error_category, "rate_limited")
            self.assertEqual(service.show(result.image_id).status, "failed")

    def test_content_boundary_rejects_minors_secrets_and_local_files_without_provider(self):
        prompts = (
            "draw explicit sexual content involving a minor",
            "draw the password and expose the secret",
            r"create an image from C:\Users\private\secret.png",
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, _, adapter, _ = self.make(tmp)
            for prompt in prompts:
                result = service.generate(prompt)
                self.assertEqual(result.status, "rejected")
            self.assertEqual(adapter.calls, [])

    def test_store_rejects_duplicate_id_and_verifies_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, _, _ = self.make(tmp)
            result = service.generate("A robot")
            with self.assertRaises(FileExistsError):
                service.store.persist(result, (ProviderImage(png(), "image/png"),))
            path = service.store.artifact_path(result.artifacts[0])
            path.write_bytes(png(extra=b"changed"))
            with self.assertRaises(ValueError):
                service.store.artifact_path(result.artifacts[0])

    def test_bounded_history_and_corrupt_index_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, _, _ = self.make(tmp, config=Config({"image.history_limit": 2}))
            ids = [service.generate(f"robot {index}").image_id for index in range(3)]
            self.assertEqual(len(service.history(10)), 2)
            service.store.index_path.write_text("corrupt", encoding="utf-8")
            recovered = service.history(10)
            self.assertEqual(len(recovered), 2)
            self.assertIn(ids[-1], {item.image_id for item in recovered})

    def test_delivery_and_copy_event_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, _, _ = self.make(tmp)
            result = service.generate("A robot")
            for index in range(150):
                service.store.record_event(result.image_id, "delivery", {"attempt": index})
            events = service.store.artifact_path(result.artifacts[0]).parent / "events.jsonl"
            self.assertLessEqual(events.stat().st_size, 65_536)
            self.assertLessEqual(len(events.read_text(encoding="utf-8").splitlines()), 100)

    def test_external_store_permissions_and_vault_sibling_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "user/vault/marker"
            marker.parent.mkdir(parents=True)
            marker.write_text("do-not-read", encoding="utf-8")
            service, _, _, _ = self.make(root)
            service.generate("A robot")
            self.assertEqual(marker.read_text(encoding="utf-8"), "do-not-read")
            if os.name != "nt":
                self.assertEqual(service.store.root.stat().st_mode & 0o077, 0)

    def test_provider_selection_override_fallback_and_text_independence(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ImageProviderRegistry()
            unavailable = FakeAdapter("primary", "provider_unavailable")
            ready = FakeAdapter("fallback")
            registry.register(unavailable)
            registry.register(ready)
            with self.assertRaises(KeyError):
                registry.register(FakeAdapter("primary"))
            config = Config({"image.provider": "primary", "providers.default": "gemini"})
            service, _, _, selected = self.make(tmp, config=config, registry=registry)
            result = service.generate("A robot")
            self.assertTrue(result.fallback_used)
            self.assertEqual(result.resolved_provider, "fallback")
            self.assertEqual(selected.get("providers.default"), "gemini")
            explicit = service.generate("A robot", provider="primary")
            self.assertEqual(explicit.status, "unavailable")
            with self.assertRaises(ImageProviderError):
                service.set_provider("unknown")
            service.set_provider("fallback")
            self.assertEqual(selected.get("image.provider"), "fallback")
            self.assertEqual(selected.get("providers.default"), "gemini")

    def test_workspace_save_confirmation_primitives_enforce_containment_no_overwrite_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, workspace, _, _ = self.make(tmp)
            result = service.generate("A robot")
            plan = service.prepare_save(result.image_id, "assets/robot.png")
            self.assertFalse(plan.exists)
            target = service.save(result.image_id, "assets/robot.png")
            self.assertEqual(target.read_bytes(), png())
            self.assertEqual(target.resolve().parent.parent, workspace.resolve())
            with self.assertRaises(FileExistsError):
                service.save(result.image_id, "assets/robot.png")
            for path in ("../escape.png", str(workspace / "absolute.png"), ".git/image.png", ".orion/image.png"):
                with self.assertRaises(ValueError):
                    service.prepare_save(result.image_id, path)

    def test_workspace_save_rejects_symlink_escape_when_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, workspace, _, _ = self.make(tmp)
            result = service.generate("A robot")
            outside = Path(tmp) / "outside"
            outside.mkdir()
            link = workspace / "linked"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Directory symlinks are unavailable")
            with self.assertRaises(ValueError):
                service.prepare_save(result.image_id, "linked/robot.png")

    def test_workspace_copy_race_never_deletes_a_file_orion_did_not_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, workspace, _, _ = self.make(tmp)
            result = service.generate("A robot")
            target = workspace / "assets/robot.png"
            original_open = Path.open

            def raced_open(path, mode="r", *args, **kwargs):
                if Path(path) == target and mode == "xb":
                    with original_open(target, "wb") as handle:
                        handle.write(b"created by another process")
                    raise FileExistsError("simulated race")
                return original_open(path, mode, *args, **kwargs)

            with patch("pathlib.Path.open", new=raced_open):
                with self.assertRaises(FileExistsError):
                    service.save(result.image_id, "assets/robot.png")
            self.assertEqual(target.read_bytes(), b"created by another process")


class FakeImagesAPI:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, images):
        self.images = images


class OpenAIImageAdapterTests(unittest.TestCase):
    def request(self):
        return ImageGenerationRequest.from_value({
            "schema_version": 1, "request_id": "request-000000000001", "prompt": "robot",
            "provider": "openai", "count": 1, "size": "1024x1024", "quality": "medium",
            "output_format": "png", "source_interface": "cli",
            "created_at": "2026-07-20T12:00:00+00:00",
        })

    def adapter(self, response=None, *, secret="secret", error=None, downloader=None, config=None):
        images = FakeImagesAPI(response, error)
        calls = []
        def factory(**kwargs):
            calls.append(kwargs)
            return FakeClient(images)
        return OpenAIImageAdapter(config or Config(), Secrets(secret), client_factory=factory, downloader=downloader), images, calls

    def test_encoded_response_uses_official_client_and_does_not_return_key(self):
        row = SimpleNamespace(b64_json=base64.b64encode(png()).decode(), url=None, revised_prompt="revised")
        adapter, api, calls = self.adapter(SimpleNamespace(data=[row], usage=SimpleNamespace(total_tokens=7)))
        output = adapter.generate(self.request())
        self.assertEqual(output.images[0].data, png())
        self.assertEqual(output.usage["total_tokens"], 7)
        self.assertEqual(api.calls[0]["model"], "gpt-image-2")
        self.assertNotIn("secret", repr(output))
        self.assertEqual(calls[0]["api_key"], "secret")

    def test_credential_missing_and_client_readiness(self):
        adapter = OpenAIImageAdapter(Config(), Secrets(""), client_factory=lambda **_: None)
        self.assertEqual(adapter.status().state, "credential_missing")
        with self.assertRaises(ImageProviderError) as raised:
            adapter.generate(self.request())
        self.assertEqual(raised.exception.category, "credential_missing")

        with patch.dict("sys.modules", {"openai": None}):
            missing = OpenAIImageAdapter(Config(), Secrets("configured"))
            self.assertEqual(missing.status().state, "client_missing")

    def test_url_response_is_bounded_validated_and_url_not_returned(self):
        response = SimpleNamespace(data=[SimpleNamespace(b64_json="", url="https://temporary.test/image", revised_prompt="")], usage=None)
        download = SimpleNamespace(
            headers={"Content-Type": "image/png"},
            raise_for_status=lambda: None,
            iter_content=lambda _size: iter((png(),)),
        )
        download_calls = []
        def downloader(*args, **kwargs):
            download_calls.append((args, kwargs))
            return download
        adapter, _, _ = self.adapter(response, downloader=downloader)
        output = adapter.generate(self.request())
        self.assertEqual(output.images[0].data, png())
        self.assertNotIn("temporary.test", repr(output))
        self.assertFalse(download_calls[0][1]["allow_redirects"])

    def test_unsafe_temporary_url_is_rejected_before_download(self):
        response = SimpleNamespace(
            data=[SimpleNamespace(b64_json="", url="https://127.0.0.1/private", revised_prompt="")],
            usage=None,
        )
        downloader = unittest.mock.Mock()
        adapter, _, _ = self.adapter(response, downloader=downloader)
        with self.assertRaises(ImageProviderError) as raised:
            adapter.generate(self.request())
        self.assertEqual(raised.exception.category, "invalid_response")
        downloader.assert_not_called()

    def test_timeout_rate_limit_moderation_and_generic_errors_are_sanitized(self):
        errors = []
        timeout_adapter, _, _ = self.adapter(error=requests.Timeout("secret-key"))
        errors.append((timeout_adapter, "timed_out"))
        rate = type("RateLimitError", (Exception,), {"status_code": 429})()
        rate_adapter, _, _ = self.adapter(error=rate)
        errors.append((rate_adapter, "rate_limited"))
        moderation = type("BadRequestError", (Exception,), {"status_code": 400, "code": "moderation_blocked"})()
        moderation_adapter, _, _ = self.adapter(error=moderation)
        errors.append((moderation_adapter, "content_rejected"))
        generic_adapter, _, _ = self.adapter(error=RuntimeError("Authorization: Bearer secret-key"))
        errors.append((generic_adapter, "generation_failed"))
        for adapter, category in errors:
            with self.assertRaises(ImageProviderError) as raised:
                adapter.generate(self.request())
            self.assertEqual(raised.exception.category, category)
            self.assertNotIn("secret-key", str(raised.exception))

    def test_invalid_encoded_media_html_and_oversized_responses_fail_closed(self):
        malformed = SimpleNamespace(data=[SimpleNamespace(b64_json="%%%", url="")], usage=None)
        adapter, _, _ = self.adapter(malformed)
        with self.assertRaises(ImageProviderError) as raised:
            adapter.generate(self.request())
        self.assertEqual(raised.exception.category, "invalid_response")

        html = SimpleNamespace(headers={"Content-Type": "text/html"}, raise_for_status=lambda: None, iter_content=lambda _: iter((b"<html>",)))
        response = SimpleNamespace(data=[SimpleNamespace(b64_json="", url="https://temporary.test/image")], usage=None)
        adapter, _, _ = self.adapter(response, downloader=lambda *args, **kwargs: html)
        with self.assertRaises(ImageProviderError) as raised:
            adapter.generate(self.request())
        self.assertEqual(raised.exception.category, "invalid_response")

        config = Config({"image.max_image_bytes": 10})
        encoded = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png()).decode(), url="")], usage=None)
        adapter, _, _ = self.adapter(encoded, config=config)
        with self.assertRaises(ImageProviderError) as raised:
            adapter.generate(self.request())
        self.assertEqual(raised.exception.category, "response_too_large")


class ImageRouterTests(unittest.TestCase, ServiceFixture):
    def orion(self, root):
        service, workspace, _, config = self.make(root)
        history = ActionHistory(workspace)
        actions = ActionService(history)
        actions.register_handler("image_save", lambda action: service.save(
            action.parameters["image_id"], action.parameters["destination"]
        ))
        actions.approval.set_policy("image_save", PolicyDecision.REQUIRE_APPROVAL, "approve")
        return SimpleNamespace(
            image_service=service, config_manager=config,
            workspace_manager=SimpleNamespace(root=workspace), action_service=actions,
            plugin_manager=SimpleNamespace(help_lines=lambda: ()),
        )

    def test_status_providers_generation_history_show_and_provider_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            orion = self.orion(tmp)
            router = CommandRouter(orion)
            with patch("builtins.print") as output:
                for command in (
                    "image", "image status", "image providers", 'image generate "friendly robot"',
                    "image history",
                ):
                    self.assertTrue(router.handle(command))
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Image Center", rendered)
            self.assertIn("Image generated", rendered)
            image_id = orion.image_service.history(1)[0].image_id
            with patch("builtins.print") as output:
                router.handle(f"image show {image_id}")
                router.handle("image provider use fake")
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn(image_id, rendered)
            self.assertIn("text provider was not changed", rendered)

    def test_save_denial_and_approval_are_explicit_and_never_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            orion = self.orion(tmp)
            router = CommandRouter(orion)
            image_id = orion.image_service.generate("robot").image_id
            with patch("builtins.input", return_value="n"), patch("builtins.print"):
                router.handle(f"image save {image_id} assets/robot.png")
            self.assertFalse((orion.workspace_manager.root / "assets/robot.png").exists())
            with patch("builtins.input", return_value="yes"), patch("builtins.print"):
                router.handle(f"image save {image_id} assets/robot.png")
            self.assertTrue((orion.workspace_manager.root / "assets/robot.png").is_file())
            with patch("builtins.input") as confirmation, patch("builtins.print"):
                router.handle(f"image save {image_id} assets/robot.png")
            confirmation.assert_not_called()

    def test_cli_rejects_absolute_traversal_and_protected_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            orion = self.orion(tmp)
            router = CommandRouter(orion)
            image_id = orion.image_service.generate("robot").image_id
            for destination in ("../bad.png", ".git/bad.png", str(Path(tmp).resolve() / "bad.png")):
                with patch("builtins.print") as output:
                    router.handle(f'image save {image_id} "{destination}"')
                self.assertIn("refused", "\n".join(str(c.args[0]) for c in output.call_args_list if c.args).lower())

    def test_generation_unavailable_is_reported_without_workspace_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            orion = self.orion(tmp)
            orion.image_service.registry._providers["fake"].state = "provider_unavailable"
            router = CommandRouter(orion)
            with patch("builtins.print") as output:
                router.handle('image generate "friendly robot"')
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Provider Unavailable", rendered)
            self.assertFalse(any(orion.workspace_manager.root.rglob("*.png")))

    def test_help_completion_and_user_guide_cover_every_image_command(self):
        commands = (
            "image", "image status", "image providers", "image provider use",
            "image generate", "image history", "image show", "image save",
        )
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        help_text = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        base = __import__("orion.ui.console", fromlist=["BASE_COMMANDS"]).BASE_COMMANDS
        guide = (Path(__file__).resolve().parents[1] / "docs/USER_GUIDE.md").read_text(encoding="utf-8")
        for command in commands:
            self.assertIn(command, base)
            self.assertIn(command, help_text)
            self.assertIn(command, guide)


class FakeDiscordFile:
    def __init__(self, path, filename):
        self.path = path
        self.filename = filename


class FakeDiscordModule:
    File = FakeDiscordFile


class DiscordImageTests(unittest.IsolatedAsyncioTestCase, ServiceFixture):
    def message(self, *, user=123, channel=456, guild=789):
        return SimpleNamespace(
            author=SimpleNamespace(id=user),
            channel=SimpleNamespace(id=channel),
            guild=None if guild is None else SimpleNamespace(id=guild),
            reply=AsyncMock(),
        )

    def interface(self, service, *, policy=None, monotonic=None):
        orion = SimpleNamespace(request_router=SimpleNamespace(route=lambda text: SimpleNamespace(source="ai", output=text)))
        return DiscordBotInterface(
            orion, "token", [123], [456], (), False,
            image_service=service,
            image_policy=policy or DiscordImagePolicy.from_values(True, [123], 30, 1000, 8_388_608, 2),
            monotonic=monotonic,
        )

    def test_deterministic_intent_accepts_explicit_forms_and_rejects_normal_image_discussion(self):
        accepted = (
            "!image a robot", "create an image of a robot", "generate a picture of Mars",
            "draw a friendly mascot", "render artwork of a castle",
        )
        rejected = (
            "What is a Docker image?", "Explain image compression.",
            "Can you inspect this image?", "Where did I save the image?", "!imagery test",
        )
        self.assertTrue(all(DiscordImageIntentDetector.detect(value) for value in accepted))
        self.assertTrue(all(DiscordImageIntentDetector.detect(value) is None for value in rejected))

    async def test_success_uploads_attachment_without_local_path_and_records_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, workspace, adapter, _ = self.make(tmp)
            interface = self.interface(service)
            message = self.message()
            with patch("orion.interfaces.discord.asyncio.to_thread", wraps=asyncio.to_thread) as offload:
                handled = await interface.handle_image_request(message, "!image a robot", FakeDiscordModule)
            self.assertTrue(handled)
            offload.assert_awaited_once()
            self.assertEqual(len(adapter.calls), 1)
            self.assertFalse(any(workspace.rglob("*.png")))
            final = message.reply.await_args_list[-1]
            caption = final.args[0]
            self.assertIn("Generated by Orion", caption)
            self.assertIn("Image ID:", caption)
            self.assertNotIn(str(service.store.root), caption)
            self.assertIsInstance(final.kwargs["file"], FakeDiscordFile)
            self.assertEqual(interface.diagnostics.replies_sent, 2)

    async def test_disabled_unauthorized_dm_empty_and_oversized_never_call_provider(self):
        scenarios = (
            (DiscordImagePolicy.from_values(False, [123]), self.message(), "!image robot"),
            (DiscordImagePolicy.from_values(True, [999]), self.message(), "!image robot"),
            (DiscordImagePolicy.from_values(True, [123]), self.message(guild=None), "!image robot"),
            (DiscordImagePolicy.from_values(True, [123]), self.message(), "!image"),
            (DiscordImagePolicy.from_values(True, [123], 30, 3), self.message(), "!image robot"),
        )
        for policy, message, prompt in scenarios:
            with tempfile.TemporaryDirectory() as tmp:
                service, _, adapter, _ = self.make(tmp)
                await self.interface(service, policy=policy).handle_image_request(message, prompt, FakeDiscordModule)
                self.assertEqual(adapter.calls, [])

    async def test_cooldown_and_channel_concurrency_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, adapter, _ = self.make(tmp)
            interface = self.interface(service, monotonic=lambda: 100.0)
            first = self.message()
            second = self.message()
            await interface.handle_image_request(first, "!image robot", FakeDiscordModule)
            await interface.handle_image_request(second, "!image robot two", FakeDiscordModule)
            self.assertEqual(len(adapter.calls), 1)
            self.assertIn("cooldown", second.reply.await_args.args[0].lower())
            interface._image_channels.add(999)
            interface.image_policy = DiscordImagePolicy.from_values(True, [123], 0, 1000, 8_388_608, 1)
            busy = self.message(channel=456)
            await interface.handle_image_request(busy, "!image robot three", FakeDiscordModule)
            self.assertIn("active", busy.reply.await_args.args[0].lower())

    async def test_oversized_upload_retains_external_artifact_and_safe_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, workspace, _, _ = self.make(tmp)
            policy = DiscordImagePolicy.from_values(True, [123], 0, 1000, 10, 2)
            message = self.message()
            await self.interface(service, policy=policy).handle_image_request(message, "!image robot", FakeDiscordModule)
            result = service.history(1)[0]
            self.assertTrue(service.store.artifact_path(result.artifacts[0]).is_file())
            self.assertFalse(any(workspace.rglob("*.png")))
            self.assertIn("exceeds", message.reply.await_args.args[0].lower())
            self.assertNotIn(str(service.store.root), message.reply.await_args.args[0])

    async def test_provider_unavailable_and_error_responses_are_sanitized(self):
        for error in (
            ImageProviderError("provider_unavailable", "secret provider body"),
            ImageProviderError("timed_out", "secret timeout body"),
            RuntimeError("Authorization: Bearer secret-token"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                service, _, _, _ = self.make(tmp, adapter=FakeAdapter(error=error))
                message = self.message()
                await self.interface(service).handle_image_request(message, "!image robot", FakeDiscordModule)
                rendered = " ".join(str(call.args[0]) for call in message.reply.await_args_list)
                self.assertNotIn("secret", rendered)
                self.assertNotIn(str(service.store.root), rendered)


if __name__ == "__main__":
    unittest.main()
