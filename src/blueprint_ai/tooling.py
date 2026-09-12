"""Explicit, container-only acquisition with durable image provenance; never global installs."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path

from blueprint_ai.safety import atomic_write_text, read_text_bounded, run_process
from blueprint_ai.sandbox import SandboxUnavailable, _local_runtime, image_identity
from blueprint_ai.support import PROVIDERS, TOOLS, ToolSpec


def tool_plan(name: str, backend: str = "docker") -> dict:
    if name not in TOOLS and name not in PROVIDERS:
        raise ValueError(f"unknown tool: {name}")
    if backend not in {"docker", "podman"}:
        raise ValueError("acquisition backend must be docker or podman")
    provider = PROVIDERS.get(name)
    spec = TOOLS.get(name)
    if spec is None and provider is not None:
        spec = ToolSpec(
            id=name, source=provider.source, license=provider.license, image=provider.image
        )
    assert spec is not None
    return {
        "tool": name,
        "source": spec.source,
        "license": spec.license,
        "backend": backend,
        "image": spec.image,
        "command": [backend, "build", "--tag", spec.image, "<isolated-context>"]
        if spec.image_recipe
        else [backend, "pull", spec.image]
        if spec.image
        else [],
        "base_image": spec.base_image,
        "image_recipe": spec.image_recipe,
        "host_packages_changed": False,
        "network": bool(spec.image),
        "state": "installable" if spec.image else "external-guidance",
        "detail": "Explicit acquisition; retain the immutable image ID; later runs never pull"
        if spec.image
        else spec.acquisition,
    }


def cache_root() -> Path:
    return Path.home() / ".cache" / "blueprint-ai" / "tools"


def install_tool(name: str, backend: str = "docker", *, cache: Path | None = None) -> dict:
    plan = tool_plan(name, backend)
    if not plan["image"]:
        raise ValueError(f"{name}: {plan['detail']}")
    destination = cache or cache_root()
    for parent in (destination, *destination.parents):
        if parent.is_symlink():
            raise ValueError("tool cache must not cross symlinks")
    prefix, env = _local_runtime(backend)
    result = run_process(
        [*prefix, "pull", plan["base_image"] or plan["image"]],
        Path(tempfile.gettempdir()),
        240,
        env=env,
        output_limit=1_000_000,
    )
    if result.returncode or result.timed_out or result.output_truncated:
        raise SandboxUnavailable("image acquisition failed: " + result.stderr[-500:])
    if plan["image_recipe"]:
        reference = run_process(
            [*prefix, "image", "inspect", plan["base_image"], "--format", "{{json .RepoDigests}}"],
            Path(tempfile.gettempdir()),
            10,
            env=env,
            output_limit=16_384,
        )
        if reference.returncode or reference.timed_out or reference.output_truncated:
            raise SandboxUnavailable("cannot pin the base image registry digest")
        try:
            digests = json.loads(reference.stdout)
            if (
                not isinstance(digests, list)
                or not digests
                or not isinstance(digests[0], str)
                or not re.fullmatch(r"[^\s@]+@sha256:[a-f0-9]{64}", digests[0])
            ):
                raise ValueError("invalid registry digest")
        except (ValueError, TypeError) as exc:
            raise SandboxUnavailable("cannot pin the base image registry digest") from exc
        base = digests[0]
        with tempfile.TemporaryDirectory(prefix="blueprint-tool-image-") as temporary:
            context = Path(temporary)
            recipe = "FROM " + base + "\n" + "\n".join(plan["image_recipe"]) + "\n"
            (context / "Dockerfile").write_text(recipe)
            built = run_process(
                [*prefix, "build", "--tag", plan["image"], str(context)],
                context,
                300,
                env=env,
                output_limit=1_000_000,
            )
            if built.returncode or built.timed_out or built.output_truncated:
                raise SandboxUnavailable("isolated tool image build failed: " + built.stderr[-800:])
            plan["base_digest"] = base
            plan["recipe_sha256"] = hashlib.sha256(recipe.encode()).hexdigest()
    receipt = {**plan, "image_id": image_identity(backend, plan["image"])}
    atomic_write_text(
        destination / f"{name}-{backend}.json",
        json.dumps(receipt, indent=2) + "\n",
        root=destination,
    )
    return receipt


def cached_image(name: str, backend: str, *, cache: Path | None = None) -> str | None:
    spec = TOOLS.get(name.split(":", 1)[0])
    if not spec or not spec.image:
        return None
    backend = "docker" if backend == "gvisor" else backend
    root = cache or cache_root()
    path = root / f"{spec.id}-{backend}.json"
    cache_tool = spec.id
    if not path.exists() and spec.acquisition_tool:
        cache_tool = spec.acquisition_tool
        path = root / f"{cache_tool}-{backend}.json"
    try:
        if any(p.is_symlink() for p in (path, *root.parents, root)):
            return None
        receipt = json.loads(read_text_bounded(path, 16_384, root=root))
        if not isinstance(receipt, dict):
            return None
        digest = receipt.get("image_id", "")
        if (
            receipt.get("image") != spec.image
            or receipt.get("tool") != cache_tool
            or receipt.get("backend") != backend
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
        ):
            return None
        return digest
    except (OSError, ValueError, TypeError):
        return None
