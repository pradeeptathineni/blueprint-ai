from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_runtime_sbom(tmp_path: Path, packages: dict[str, str]) -> tuple[Path, Path]:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps([{"name": name, "version": version} for name, version in packages.items()])
    )
    sbom = tmp_path / "runtime.spdx.json"
    sbom.write_text(
        json.dumps(
            {
                "spdxVersion": "SPDX-2.3",
                "SPDXID": "SPDXRef-DOCUMENT",
                "documentNamespace": "https://example.test/spdx/runtime",
                "creationInfo": {"creators": ["Tool: syft-1.42.3"]},
                "relationships": [
                    {
                        "spdxElementId": "SPDXRef-DOCUMENT",
                        "relatedSpdxElement": "SPDXRef-runtime",
                        "relationshipType": "DESCRIBES",
                    }
                ],
                "packages": [
                    {"name": name, "versionInfo": version} for name, version in packages.items()
                ],
            }
        )
    )
    return sbom, inventory


def test_runtime_sbom_verifier_enforces_exact_clean_inventory(tmp_path: Path) -> None:
    verifier = _load_module("verify_sbom_test", ROOT / "benchmarks" / "verify_sbom.py")
    sbom, inventory = _write_runtime_sbom(
        tmp_path, {"blueprint-ai-cli": "0.9.1", "typer": "0.21.0"}
    )

    result = verifier.verify_sbom(sbom, inventory, "blueprint-ai-cli", "0.9.1", [str(ROOT)])

    assert result["runtime_packages"] == 2


def test_runtime_sbom_verifier_rejects_development_packages(tmp_path: Path) -> None:
    verifier = _load_module("verify_sbom_dev_test", ROOT / "benchmarks" / "verify_sbom.py")
    sbom, inventory = _write_runtime_sbom(
        tmp_path, {"blueprint-ai-cli": "0.9.1", "pytest": "9.0.2"}
    )

    with pytest.raises(ValueError, match="development-only packages: pytest"):
        verifier.verify_sbom(sbom, inventory, "blueprint-ai-cli", "0.9.1", [])


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def test_pypi_verifier_matches_metadata_membership_and_downloaded_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier = _load_module("verify_pypi_test", ROOT / "benchmarks" / "verify_pypi.py")
    version = "0.9.1"
    files = {
        f"blueprint_ai_cli-{version}-py3-none-any.whl": b"wheel bytes",
        f"blueprint_ai_cli-{version}.tar.gz": b"sdist bytes",
    }
    digests = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    checksums = tmp_path / "SHA256SUMS"
    checksums.write_text("".join(f"{digest}  {name}\n" for name, digest in digests.items()))
    project_urls = {
        "Changelog": "https://github.com/pradeeptathineni/blueprint-ai/blob/main/CHANGELOG.md",
        "Documentation": "https://github.com/pradeeptathineni/blueprint-ai#readme",
        "Issues": "https://github.com/pradeeptathineni/blueprint-ai/issues",
        "Repository": "https://github.com/pradeeptathineni/blueprint-ai",
        "Release validation": (
            "https://github.com/pradeeptathineni/blueprint-ai/blob/"
            f"{version}/docs/release-validation-{version}.md"
        ),
    }
    rows = [
        {
            "filename": name,
            "digests": {"sha256": digest},
            "yanked": False,
            "url": f"https://files.pythonhosted.org/packages/{name}",
        }
        for name, digest in digests.items()
    ]
    metadata = {
        "info": {
            "name": "blueprint-ai-cli",
            "version": version,
            "requires_python": ">=3.12",
            "project_urls": project_urls,
        },
        "urls": rows,
    }

    def fake_urlopen(url: str, **_kwargs: Any) -> _Response:
        if url.endswith("/json"):
            return _Response(json.dumps(metadata).encode())
        name = url.rsplit("/", 1)[-1]
        return _Response(files[name])

    urlopen: Callable[..., _Response] = fake_urlopen
    monkeypatch.setattr(verifier.urllib.request, "urlopen", urlopen)

    result = verifier.verify_pypi("blueprint-ai-cli", version, checksums, 1, 0)

    assert result["files"] == digests
