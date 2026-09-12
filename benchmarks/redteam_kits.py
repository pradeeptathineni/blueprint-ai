"""Live create/conflict/idempotence/rollback gate for every shipped capability kit."""

import argparse
import hashlib
import json
from pathlib import Path

from blueprint_ai.capabilities import add_capability, plan_add
from blueprint_ai.discovery import discover_project
from blueprint_ai.remediation import KITS, rollback_operation
from blueprint_ai.sandbox import SandboxPolicy


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else "directory"
        for path in root.rglob("*")
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pytest-image", default="blueprint-redteam/pytest:9.1.1")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    images = {
        "testing-python": args.pytest_image,
        "testing-javascript": "node:24-bookworm-slim",
        "terraform": "hashicorp/terraform:1.16.2",
    }
    rows = []
    for name, kit in KITS.items():
        root = (args.output / name).resolve()
        root.mkdir()
        for rel, content in {
            "README.md": "# Controlled kit audit\n",
            "pyproject.toml": '[project]\nname = "example"\nversion = "1.0"\n',
            "package.json": '{"name":"example"}\n',
            "example.py": "VALUE = 1\n",
            "index.js": "export const value = 1;\n",
            "main.tf": 'terraform {\n  required_version = ">= 1.8"\n}\n',
        }.items():
            (root / rel).write_text(content)
        facts = discover_project(root)
        before = snapshot(root)
        policy = SandboxPolicy(backend="docker", image=images.get(name))
        plan = plan_add(root, name)
        assert not plan["conflicts"]
        result = add_capability(root, facts, name, policy=policy)
        assert len(result.changed) == len(kit.files), result
        assert result.verifications[0].status == "passed", result
        if name in images:
            assert all(v.status == "passed" for v in result.verifications), result
        second = add_capability(root, facts, name, policy=policy)
        assert not second.changed and second.operation_id is None
        assert result.operation_id
        rolled = rollback_operation(root, result.operation_id)
        assert snapshot(root) == before, name
        conflict_path = root / next(iter(kit.files))
        conflict_path.parent.mkdir(parents=True, exist_ok=True)
        conflict_path.write_text("User-owned configuration\n")
        before_conflict = snapshot(root)
        try:
            add_capability(root, facts, name, policy=policy)
        except ValueError:
            pass
        else:
            raise AssertionError("conflict unexpectedly accepted: " + name)
        assert snapshot(root) == before_conflict
        row = {
            "kit": name,
            "status": "passed",
            "checks": ["plan", "create", "parse", "idempotence", "exact rollback", "conflict"],
            "application": result.model_dump(mode="json"),
            "rollback": rolled.model_dump(mode="json"),
        }
        rows.append(row)
        (args.output / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(json.dumps({"kit": name, "verifiers": [v.status for v in result.verifications]}))


if __name__ == "__main__":
    main()
