"""Reproduce Linux test suites and fresh wheel/sdist installation smoke checks.

Uses already-local official Python images. Linux suites build temporary Git-enabled images;
only their unique tags are removed. Dependency downloads require the operator's explicit run.
Native installations use existing Python interpreters and disposable environments, not globals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from blueprint_ai.engine import analyzer_identity
from blueprint_ai.safety import controlled_env, run_process
from blueprint_ai.sandbox import SandboxPolicy, _local_runtime, execute, image_identity

VERSIONS = ("3.12", "3.13", "3.14")
RESULT = "BLUEPRINT_PLATFORM_RESULT="
LINUX_TEST_SCRIPT = r"""
import json, pathlib, platform, subprocess, sys, xml.etree.ElementTree as ET
subprocess.run(['python', '-m', 'venv', '/tmp/fresh'], check=True)
python = '/tmp/fresh/bin/python'
for command in (
    [python, '-m', 'pip', 'install', '--disable-pip-version-check', '--require-hashes',
     '-r', 'requirements.txt'],
    [python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-deps', '.'],
):
    installed = subprocess.run(command, capture_output=True, text=True)
    if installed.returncode:
        print(installed.stdout + installed.stderr)
        sys.exit(installed.returncode)
result = subprocess.run([
    python, '-m', 'pytest', '-o', 'addopts=', '-q', '-p', 'no:cacheprovider',
    '-W', 'error::DeprecationWarning', '--junitxml=/tmp/tests.xml', 'tests',
])
xml = pathlib.Path('/tmp/tests.xml').read_text()
summary = ET.fromstring(xml).find('testsuite').attrib
print('BLUEPRINT_PLATFORM_RESULT=' + json.dumps({
    'python': platform.python_version(), 'platform': platform.platform(),
    'git': subprocess.check_output(['git', '--version'], text=True).strip(),
    'tests': summary, 'junit': xml,
}))
sys.exit(result.returncode)
"""
INSTALL_SCRIPT = r"""
import json, pathlib, subprocess, sys
subprocess.run(['python', '-m', 'venv', '/tmp/fresh'], check=True)
python = '/tmp/fresh/bin/python'
for command in (
    [python, '-m', 'pip', 'install', '--disable-pip-version-check', sys.argv[1]],
    [python, '/workspace/release_smoke.py', '--output', '/tmp/smoke.json'],
):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        print(result.stdout + result.stderr)
        sys.exit(result.returncode)
smoke = json.loads(pathlib.Path('/tmp/smoke.json').read_text())
print('BLUEPRINT_PLATFORM_RESULT=' + json.dumps(smoke))
"""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Gate:
    def __init__(self, source: Path, output: Path, uv: str):
        self.source, self.output, self.uv = source, output, uv
        self.summary: dict[str, Any] = {
            "analyzer": analyzer_identity(),
            "source": str(source),
            "host_platform": platform.platform(),
            "rows": [],
        }
        self.prefix, self.env = _local_runtime("docker")
        self.native_env = controlled_env(
            {"UV_CACHE_DIR": str(output / "uv-cache"), "UV_PYTHON_DOWNLOADS": "never"}
        )

    def save(self, row: dict[str, Any]) -> None:
        self.summary["rows"].append(row)
        self.flush()
        print(json.dumps({key: row[key] for key in ("environment", "kind", "passed")}), flush=True)

    def flush(self) -> None:
        (self.output / "summary.json").write_text(json.dumps(self.summary, indent=2) + "\n")

    def host(self, label: str, command: list[str], cwd: Path, timeout: int = 300) -> None:
        env = self.env if command[0] == self.prefix[0] else self.native_env
        result = run_process(command, cwd, timeout, env=env)
        (self.output / f"{label}.log").write_text(result.stdout + result.stderr)
        if result.returncode or result.timed_out or result.output_truncated:
            raise RuntimeError(f"{label} failed; inspect {self.output / (label + '.log')}")

    def container(self, label: str, image: str, root: Path, command: list[str]) -> dict[str, Any]:
        execution = execute(
            command,
            root,
            SandboxPolicy(
                backend="docker",
                image=image,
                trusted=True,
                network="unrestricted",
                authorize_network=True,
                timeout=600,
                memory_mb=3072,
                scratch_mb=2048,
                pids=256,
            ),
        )
        (self.output / f"{label}.stdout").write_text(execution.result.stdout)
        (self.output / f"{label}.stderr").write_text(execution.result.stderr)
        payload = next(
            (
                json.loads(line[len(RESULT) :])
                for line in execution.result.stdout.splitlines()
                if line.startswith(RESULT)
            ),
            None,
        )
        row = {
            "environment": label,
            "kind": "linux-tests" if label.startswith("suite") else "install",
            "passed": execution.result.returncode == 0 and payload is not None,
            "exit_code": execution.result.returncode,
            "evidence": execution.evidence.model_dump(mode="json"),
            "result": payload,
        }
        if payload and "junit" in payload:
            (self.output / f"{label}.junit.xml").write_text(payload.pop("junit"))
        return row

    def linux_tests(self, requirements: Path | None) -> None:
        snapshot = self.output / "source"
        snapshot.mkdir()
        for name in ("src", "tests", "docs", "benchmarks", ".github"):
            shutil.copytree(
                self.source / name,
                snapshot / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        for name in (
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "LICENSE",
            ".markdownlint-cli2.yaml",
        ):
            shutil.copy2(self.source / name, snapshot / name)
        if requirements:
            shutil.copy2(requirements, snapshot / "requirements.txt")
        else:
            self.host(
                "export",
                [
                    self.uv,
                    "export",
                    "--locked",
                    "--extra",
                    "dev",
                    "--extra",
                    "model",
                    "--no-emit-project",
                    "--format",
                    "requirements-txt",
                    "--output-file",
                    str(snapshot / "requirements.txt"),
                ],
                self.source,
            )
        self.summary["snapshot_files"] = {
            path.relative_to(snapshot).as_posix(): digest(path)
            for path in sorted(snapshot.rglob("*"))
            if path.is_file()
        }
        self.flush()
        for version in VERSIONS:
            base = f"python:{version}-slim-bookworm"
            base_id = image_identity("docker", base)  # Never implicitly acquire a missing base.
            tag = f"blueprint-audit/python-git-{version}:{uuid.uuid4().hex}"
            row: dict[str, Any] = {
                "environment": f"suite-linux-{version}",
                "kind": "linux-tests",
                "passed": False,
                "base_image": base,
                "base_image_id": base_id,
                "temporary_image": tag,
            }
            try:
                with tempfile.TemporaryDirectory(prefix="blueprint-audit-image-") as temporary:
                    context = Path(temporary)
                    recipe = (
                        f"FROM {base}\n"
                        "RUN apt-get update && apt-get install -y --no-install-recommends git "
                        "&& rm -rf /var/lib/apt/lists/*\n"
                    )
                    (context / "Dockerfile").write_text(recipe)
                    row["recipe_sha256"] = hashlib.sha256(recipe.encode()).hexdigest()
                    self.host(
                        f"build-{version}",
                        [
                            *self.prefix,
                            "build",
                            "--pull=false",
                            "--tag",
                            tag,
                            str(context),
                        ],
                        context,
                        600,
                    )
                row.update(
                    self.container(
                        f"suite-linux-{version}", tag, snapshot, ["python", "-c", LINUX_TEST_SCRIPT]
                    )
                )
            except (OSError, RuntimeError, ValueError) as exc:
                row.update(passed=False, error=str(exc))
            finally:
                cleanup = run_process(
                    [*self.prefix, "image", "rm", tag], self.output, 30, env=self.env
                )
                row["temporary_tag_removed"] = cleanup.returncode == 0
                if cleanup.returncode:
                    row.update(passed=False, cleanup_error=cleanup.stderr)
                self.save(row)

    def installs(self) -> None:
        artifacts = []
        for pattern in ("blueprint_ai-*-py3-none-any.whl", "blueprint_ai-*.tar.gz"):
            matches = list((self.source / "dist").glob(pattern))
            if len(matches) != 1:
                raise ValueError(f"dist must contain exactly one {pattern}")
            artifacts.append(matches[0])
        stage = self.output / "artifacts"
        stage.mkdir()
        for path in (*artifacts, self.source / "benchmarks/release_smoke.py"):
            shutil.copy2(path, stage / path.name)
        self.summary["artifacts"] = {path.name: digest(path) for path in artifacts}
        self.flush()
        for version in VERSIONS:
            image = f"python:{version}-slim-bookworm"
            image_identity("docker", image)
            for artifact in artifacts:
                row = self.container(
                    f"install-linux-{version}-{artifact.suffix}",
                    image,
                    stage,
                    ["python", "-c", INSTALL_SCRIPT, f"/workspace/{artifact.name}"],
                )
                self.save({**row, "artifact": artifact.name})
        for version in VERSIONS:
            candidates = [
                shutil.which(f"python{version}"),
                str(Path.home() / ".local/bin" / f"python{version}"),
            ]
            python = next(
                (str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), None
            )
            if python is None:
                raise RuntimeError(
                    f"native Python {version} is not installed; no automatic download"
                )
            for artifact in artifacts:
                label = f"install-{platform.system().lower()}-{version}-{artifact.suffix}"
                environment = self.output / f"venv-{version}-{artifact.suffix}"
                self.host(
                    label + "-venv",
                    [self.uv, "venv", "--python", python, str(environment)],
                    self.output,
                )
                interpreter = environment / "bin/python"
                self.host(
                    label + "-pip",
                    [
                        self.uv,
                        "pip",
                        "install",
                        "--python",
                        str(interpreter),
                        str(stage / artifact.name),
                    ],
                    self.output,
                )
                smoke = self.output / f"{label}.smoke.json"
                result = run_process(
                    [str(interpreter), str(stage / "release_smoke.py"), "--output", str(smoke)],
                    self.output,
                    180,
                    env=controlled_env(),
                )
                (self.output / f"{label}.stdout").write_text(result.stdout)
                (self.output / f"{label}.stderr").write_text(result.stderr)
                self.save(
                    {
                        "environment": label,
                        "kind": "install",
                        "artifact": artifact.name,
                        "passed": result.returncode == 0,
                        "result": json.loads(smoke.read_text()) if smoke.exists() else None,
                    }
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--linux-tests", action="store_true")
    parser.add_argument("--installs", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--requirements", type=Path, help="Optional pre-exported hashed requirements"
    )
    parser.add_argument("--uv", help="Path to the existing uv executable")
    args = parser.parse_args()
    if not (args.linux_tests or args.installs):
        parser.error("select --linux-tests, --installs, or both")
    source, output = args.source.resolve(), args.output.resolve()
    if output.is_relative_to(source):
        parser.error("--output must be outside the source checkout")
    uv = args.uv or shutil.which("uv") or str(source / ".venv/bin/uv")
    output.mkdir(parents=True, exist_ok=False)
    gate = Gate(source, output, uv)
    if args.linux_tests:
        gate.linux_tests(args.requirements)
    if args.installs:
        gate.installs()
    raise SystemExit(0 if all(row["passed"] for row in gate.summary["rows"]) else 1)


if __name__ == "__main__":
    main()
