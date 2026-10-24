"""Pinned local questionnaire templates in the closed ``copier-safe-v1`` dialect.

Narrow reproduction of selected Copier a00a46b27441fceec9b15bcf3b90e4a123360ce3 behavior
(MIT, Copyright (c) 2011 Juan-Pablo Scaletti; see TEMPLATE_UPDATE_NOTICES.md): question/setting
split and short question form (``copier/_template.py`` ``filter_config``), ``DEFAULT_EXCLUDE``
and ``DEFAULT_TEMPLATES_SUFFIX``, and question type/default/choice handling
(``copier/_user_data.py`` ``Question``). Answers are typed strictly instead of cast, and only
``{{ name }}`` substitution is supported. Jinja, prompts, tasks, migrations and external data
are not implemented; declarations that need them are refused.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
from pathlib import Path

import yaml

from .template_update import MAX_FILE, MAX_TOTAL, RECORD, _commit, _create, _git, _json, _sha

DIALECT = "copier-safe-v1"
CONFIG_NAMES = ("copier.yml", "copier.yaml")
MAX_CONFIG = 64 * 1024
MAX_QUESTIONS = 64
MAX_CHOICES = 64
MAX_FILES = 1000
MAX_STRING = 4096
MAX_INT = 2**63 - 1
MAX_DIFF = 256 * 1024
# Copied from Copier DEFAULT_EXCLUDE; matched against each path component.
EXCLUDE = (
    "copier.yaml",
    "copier.yml",
    "~*",
    "*.py[co]",
    "__pycache__",
    ".git",
    ".DS_Store",
    ".svn",
)
SETTINGS = {"_subdirectory", "_templates_suffix", "_min_copier_version"}
QUESTION_FIELDS = {"type", "help", "default", "choices"}
TYPES = ("str", "bool", "int")
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_OPEN = re.compile(r"\{\{|\{%|\{#")
_VARIABLE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*\}\}")


class _Loader(yaml.SafeLoader):
    pass


def _mapping(loader: _Loader, node: yaml.MappingNode) -> dict:
    keys: set[str] = set()
    for key_node, _value in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            raise ValueError("YAML merge keys are not supported")
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise ValueError("YAML mapping keys must be strings")
        if key in keys:
            raise ValueError(f"duplicate YAML key: {key}")
        keys.add(key)
    return loader.construct_mapping(node, deep=True)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _parse(raw: bytes) -> dict:
    if len(raw) > MAX_CONFIG:
        raise ValueError("template config exceeds 64 KiB")
    try:
        text = raw.decode("utf-8")
        documents = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            documents += isinstance(event, yaml.DocumentStartEvent)
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise ValueError("YAML anchors and aliases are not supported")
            if getattr(event, "tag", None) is not None:
                raise ValueError(f"YAML tag {event.tag} is not supported")
        if documents > 1:
            raise ValueError("multiple YAML documents are not supported")
        data = yaml.load(text, Loader=_Loader)  # noqa: S506 - SafeLoader subclass
    except UnicodeDecodeError as exc:
        raise ValueError("template config is not UTF-8") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid template config YAML: {exc}") from exc
    except RecursionError as exc:
        raise ValueError("template config is nested too deeply") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("template config must be a mapping")
    return data


def _dynamic(value: object) -> bool:
    return isinstance(value, str) and _OPEN.search(value) is not None


def _typed(kind: str, value: object) -> bool:
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool) and abs(value) <= MAX_INT
    return isinstance(value, str) and len(value) <= MAX_STRING and "\0" not in value


def _same(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def _choices(name: str, kind: str, raw: object) -> list[dict]:
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, list):
        pairs = [
            tuple(item) if isinstance(item, list) and len(item) == 2 else (item, item)
            for item in raw
        ]
    else:
        raise ValueError(f"{name}: choices must be a literal list or mapping")
    if not pairs or len(pairs) > MAX_CHOICES:
        raise ValueError(f"{name}: choices must have 1 to {MAX_CHOICES} entries")
    result: list[dict] = []
    for label, value in pairs:
        if label is value and not isinstance(label, str):
            label = str(label)
        if not isinstance(label, str) or _dynamic(label) or _dynamic(value):
            raise ValueError(f"{name}: choice labels and values must be literal")
        if not _typed(kind, value):
            raise ValueError(f"{name}: choice {label!r} is not a {kind}")
        if any(_same(value, c["value"]) or label == c["label"] for c in result):
            raise ValueError(f"{name}: duplicate choice {label!r}")
        result.append({"label": label, "value": value})
    return result


def _declarations(data: dict) -> tuple[dict, list[dict], list[str]]:
    settings = {"subdirectory": "", "templates_suffix": ".jinja", "min_copier_version": None}
    declared: list[dict] = []
    refusals: list[str] = []
    for key, value in data.items():
        if key.startswith("_"):
            if key not in SETTINGS:
                refusals.append(f"{key}: unsupported template setting")
            elif not isinstance(value, str) or _dynamic(value):
                refusals.append(f"{key}: must be a literal string")
            else:
                settings[key[1:]] = value
            continue
        if not _NAME.fullmatch(key) or key in {"now", "make_secret"}:
            refusals.append(f"{key}: unsupported question name")
            continue
        spec = value if isinstance(value, dict) else {"default": value}
        extra = sorted(set(spec) - QUESTION_FIELDS)
        if extra:
            refusals.append(f"{key}: unsupported question fields: {', '.join(extra)}")
            continue
        kind = spec.get("type")
        if kind is None and "default" in spec:
            kind = {bool: "bool", int: "int", str: "str"}.get(type(spec["default"]))
        if kind not in TYPES:
            refusals.append(f"{key}: type must be declared as str, bool or int")
            continue
        question: dict = {"name": key, "type": kind, "help": spec.get("help", "")}
        if not isinstance(question["help"], str) or _dynamic(question["help"]):
            refusals.append(f"{key}: help must be a literal string")
            continue
        try:
            if "choices" in spec:
                question["choices"] = _choices(key, kind, spec["choices"])
            if "default" in spec:
                default = spec["default"]
                if not _typed(kind, default) or _dynamic(default):
                    raise ValueError(f"{key}: default must be a literal {kind}")
                if "choices" in question and not any(
                    _same(default, c["value"]) for c in question["choices"]
                ):
                    raise ValueError(f"{key}: default is not one of the choices")
                question["default"] = default
        except ValueError as exc:
            refusals.append(str(exc))
            continue
        question["required"] = "default" not in question
        declared.append(question)
    if len(declared) > MAX_QUESTIONS:
        refusals.append(f"template declares more than {MAX_QUESTIONS} questions")
    subdirectory = settings["subdirectory"]
    if not isinstance(subdirectory, str):
        raise ValueError("_subdirectory must be a literal string")
    subdirectory = subdirectory.strip("/")
    parts = subdirectory.split("/") if subdirectory else []
    if any(p in {"", ".", ".."} or "\\" in p for p in parts):
        refusals.append("_subdirectory must be a plain relative path")
    settings["subdirectory"] = subdirectory
    suffix = settings["templates_suffix"]
    if suffix and (not suffix.startswith(".") or "/" in suffix or len(suffix) > 32):
        refusals.append("_templates_suffix must be empty or a short '.suffix'")
    return settings, declared, refusals


def _tree(source: Path, commit: str) -> list[tuple[str, str, str, str, int]]:
    raw = _git(source, "ls-tree", "-rlz", "--full-tree", commit)
    entries = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        header, path = item.split(b"\t", 1)
        mode, kind, oid, size = header.decode().split()
        entries.append((path.decode("utf-8"), mode, kind, oid, -1 if size == "-" else int(size)))
    return entries


def _inspect(source: Path, commit: str) -> tuple[list, str, dict, list[dict], list[str]]:
    entries = _tree(source, commit)
    found = [entry for entry in entries if entry[0] in CONFIG_NAMES]
    if not found:
        raise ValueError("template has no copier.yml or copier.yaml at its root")
    if len(found) > 1:
        raise ValueError("template has both copier.yml and copier.yaml")
    name, mode, kind, oid, size = found[0]
    if kind != "blob" or mode not in {"100644", "100755"}:
        raise ValueError(f"{name} must be a regular file")
    if size > MAX_CONFIG:
        raise ValueError("template config exceeds 64 KiB")
    settings, declared, refusals = _declarations(_parse(_git(source, "cat-file", "blob", oid)))
    return entries, name, settings, declared, refusals


def _qualified(source: Path, commit: str) -> tuple[list, str, dict, list[dict]]:
    entries, name, settings, declared, refusals = _inspect(source, commit)
    if refusals:
        raise ValueError(f"template is outside {DIALECT}: " + "; ".join(refusals))
    return entries, name, settings, declared


def _resolve(declared: list[dict], answers: object) -> tuple[dict, dict]:
    if not isinstance(answers, dict) or any(not isinstance(k, str) for k in answers):
        raise ValueError("answers must be a JSON object")
    names = {q["name"] for q in declared}
    unknown = sorted(set(answers) - names)
    if unknown:
        raise ValueError(f"unknown answers: {', '.join(unknown)}")
    resolved, sources, missing = {}, {}, []
    for question in declared:
        name, kind = question["name"], question["type"]
        if name in answers:
            value, sources[name] = answers[name], "explicit"
        elif "default" in question:
            value, sources[name] = question["default"], "default"
        else:
            missing.append(name)
            continue
        if not _typed(kind, value):
            raise ValueError(f"answer {name} must be a JSON {kind}, got {type(value).__name__}")
        if "choices" in question and not any(_same(value, c["value"]) for c in question["choices"]):
            raise ValueError(f"answer {name} is not one of the choices")
        resolved[name] = value
    if missing:
        raise ValueError(f"missing required answers: {', '.join(missing)}")
    return resolved, sources


def _substitute(text: str, answers: dict, where: str) -> str:
    for match in _OPEN.finditer(text):
        variable = _VARIABLE.match(text, match.start())
        if match.group() != "{{" or variable is None:
            line = text.count("\n", 0, match.start()) + 1
            raise ValueError(f"{where}:{line}: unsupported template expression")
        if variable.group(1) not in answers:
            raise ValueError(f"{where}: unknown template variable {variable.group(1)}")

    def value(match: re.Match[str]) -> str:
        return str(answers[match.group(1)])

    return _VARIABLE.sub(value, text)


def _component(name: str, where: str) -> str:
    if (
        name in {"", ".", "..", ".git"}
        or any(c in name for c in "/\\")
        or any(ord(c) < 32 for c in name)
        or len(name.encode()) > 255
    ):
        raise ValueError(f"{where}: rendered path component {name!r} is unsafe")
    return name


def _render(
    source: Path, entries: list, settings: dict, answers: dict
) -> tuple[dict[str, tuple[bytes, int]], list[dict], list[str]]:
    prefix, suffix = settings["subdirectory"], settings["templates_suffix"]
    files: dict[str, tuple[bytes, int]] = {}
    rows: list[dict] = []
    excluded: list[str] = []
    folded: dict[str, str] = {}
    total = 0
    for path, mode, kind, oid, size in entries:
        if prefix and not path.startswith(prefix + "/"):
            continue
        parts = path[len(prefix) + 1 :].split("/") if prefix else path.split("/")
        if any(fnmatch.fnmatchcase(p, pattern) for p in parts for pattern in EXCLUDE):
            excluded.append(path)
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"template contains unsupported entry: {path}")
        if size > MAX_FILE:
            raise ValueError(f"template file exceeds 4 MiB: {path}")
        rendered = [_substitute(p, answers, path) for p in parts]
        templated = suffix == "" or parts[-1].endswith(suffix)
        if suffix and templated:
            rendered[-1] = rendered[-1][: -len(suffix)]
        target = "/".join(_component(p, path) for p in rendered)
        if rendered[0] == ".blueprint-ai":
            raise ValueError(f"{path}: renders into reserved .blueprint-ai")
        if target.casefold() in folded:
            raise ValueError(f"{path} and {folded[target.casefold()]} both render to {target}")
        folded[target.casefold()] = path
        if len(folded) > MAX_FILES:
            raise ValueError(f"template renders more than {MAX_FILES} files")
        content = _git(source, "cat-file", "blob", oid)
        if templated:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                if suffix:
                    raise ValueError(f"template file is not UTF-8: {path}") from exc
                templated = False
            else:
                if suffix or "\0" not in text:
                    content = _substitute(text, answers, path).encode()
                else:
                    templated = False
        if len(content) > MAX_FILE:
            raise ValueError(f"rendered template file exceeds 4 MiB: {path}")
        total += len(content)
        if total > MAX_TOTAL:
            raise ValueError("rendered template exceeds 32 MiB")
        files[target] = (content, int(mode, 8) & 0o777)
        rows.append(
            {
                "path": target,
                "source_path": path,
                "mode": files[target][1],
                "size": len(content),
                "sha256": _sha(content),
                "rendered": templated,
            }
        )
    for target in folded:
        parts = target.split("/")
        for depth in range(1, len(parts)):
            if "/".join(parts[:depth]) in folded:
                raise ValueError(f"rendered file and directory collide: {'/'.join(parts[:depth])}")
    if not files:
        raise ValueError("template has no files")
    rows.sort(key=lambda row: row["path"])
    return files, rows, sorted(excluded)


def questions(source: Path, ref: str) -> dict:
    """Return typed declarations and dialect qualification without writing anything."""
    source = source.resolve()
    commit = _commit(source, ref)
    result: dict = {"dialect": DIALECT, "source": str(source), "ref": ref, "commit": commit}
    try:
        _entries, name, settings, declared, refusals = _inspect(source, commit)
    except ValueError as exc:
        return result | {"supported": False, "refusals": [str(exc)], "questions": []}
    return result | {
        "config_path": name,
        "subdirectory": settings["subdirectory"],
        "templates_suffix": settings["templates_suffix"],
        "min_copier_version": settings["min_copier_version"],
        "supported": not refusals,
        "refusals": refusals,
        "questions": declared,
    }


def _prepare(
    source: Path, target: Path, ref: str, answers: object
) -> tuple[dict, dict[str, tuple[bytes, int]], dict]:
    if Path(os.path.abspath(target)).is_symlink():
        raise ValueError("template destination must not be a symlink")
    source, target = source.resolve(), target.resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("template destination must be empty")
    commit = _commit(source, ref)
    entries, name, settings, declared = _qualified(source, commit)
    resolved, sources = _resolve(declared, answers)
    files, rows, excluded = _render(source, entries, settings, resolved)
    record = {
        "schema": 2,
        "format": DIALECT,
        "source": str(source),
        "commit": commit,
        "answers": resolved,
    }
    binding = {
        "dialect": DIALECT,
        "source": str(source),
        "target": str(target),
        "ref": ref,
        "commit": commit,
        "config_path": name,
        "subdirectory": settings["subdirectory"],
        "templates_suffix": settings["templates_suffix"],
        "answers": resolved,
        "answer_sources": sources,
        "excluded": excluded,
        "files": rows,
        "file_count": len(rows),
        "total_bytes": sum(row["size"] for row in rows),
        "record": RECORD,
        "record_sha256": _sha(_json(record)),
    }
    diffs = []
    for row in rows:
        content = files[row["path"]][0]
        diff = None
        if len(content) <= MAX_DIFF and b"\0" not in content:
            try:
                lines = content.decode("utf-8").splitlines(keepends=True)
            except UnicodeDecodeError:
                lines = None
            if lines is not None:
                diff = "".join(difflib.unified_diff([], lines, "/dev/null", f"new/{row['path']}"))
        diffs.append({"path": row["path"], "diff": diff})
    result = binding | {
        "questions": declared,
        "diffs": diffs,
        "preview_sha256": _sha(_json(binding)),
    }
    return result, files, record


def preview(source: Path, target: Path, ref: str, answers: object) -> dict:
    """Render in memory and return the exact bound preview; the target is not created."""
    return _prepare(source, target, ref, answers)[0]


def create(source: Path, target: Path, ref: str, answers: object, confirm_digest: str) -> dict:
    """Create the project only when ``confirm_digest`` matches a fresh identical preview."""
    result, files, record = _prepare(source, target, ref, answers)
    if confirm_digest != result["preview_sha256"]:
        raise ValueError(
            "preview digest does not match the current source, answers, target and rendering;"
            " run template preview again"
        )
    _create(Path(result["target"]), files, record)
    return {
        "status": "initialized",
        "dialect": DIALECT,
        "commit": result["commit"],
        "answers": result["answers"],
        "files": sorted(files),
        "record": RECORD,
        "preview_sha256": result["preview_sha256"],
    }


def render_for_record(
    source: Path, commit: str, recorded: dict
) -> tuple[dict[str, tuple[bytes, int]], dict]:
    """Render a recorded intake at ``commit``; unsupported question transitions refuse."""
    try:
        entries, _name, settings, declared = _qualified(source, commit)
        kinds = {q["name"]: q["type"] for q in declared}
        for name, value in recorded.items():
            if name not in kinds:
                raise ValueError(f"recorded answer {name} has no question")
            if not _typed(kinds[name], value):
                raise ValueError(f"recorded answer {name} is not a {kinds[name]}")
        answers, _sources = _resolve(declared, recorded)
        files, _rows, _excluded = _render(source, entries, settings, answers)
    except ValueError as exc:
        raise ValueError(
            f"template commit {commit[:12]} is unsupported for this project: {exc}"
        ) from exc
    return files, answers
