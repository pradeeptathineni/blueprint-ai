import json
from pathlib import Path

from blueprint_ai.adapters.base import (
    CommandResult,
    ExternalToolAdapter,
    parse_checkov,
    parse_osv,
    parse_ruff,
    parse_terraform,
    parse_trivy,
)


def test_ruff_parser_normalizes_location(tmp_path: Path) -> None:
    adapter = ExternalToolAdapter("ruff", "code-quality", [], parse_ruff)
    payload = [
        {
            "code": "E501",
            "message": "too long",
            "filename": "a.py",
            "location": {"row": 7, "column": 2},
            "fix": None,
        }
    ]
    findings = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)
    assert findings[0].message == "E501: too long"
    assert findings[0].range is not None
    assert findings[0].range.start_line == 7


def test_osv_parser_normalizes_vulnerability(tmp_path: Path) -> None:
    adapter = ExternalToolAdapter("osv-scanner", "security", [], parse_osv)
    payload = {
        "results": [
            {
                "source": {"path": "requirements.txt"},
                "packages": [
                    {
                        "package": {"name": "demo"},
                        "vulnerabilities": [{"id": "OSV-1", "aliases": ["CVE-1"]}],
                    }
                ],
            }
        ]
    }
    findings = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)
    assert findings[0].category == "dependency-vulnerability"
    assert findings[0].file == "requirements.txt"


def test_terraform_parser_handles_diagnostics(tmp_path: Path) -> None:
    adapter = ExternalToolAdapter("terraform", "iac", [], parse_terraform)
    payload = {
        "valid": False,
        "diagnostics": [
            {
                "severity": "error",
                "summary": "bad",
                "detail": "broken",
                "range": {"filename": "main.tf", "start": {"line": 2}},
            }
        ],
    }
    findings = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)
    assert findings[0].severity == "high"
    assert findings[0].range is not None
    assert findings[0].range.start_line == 2


def test_trivy_parser_normalizes_scanner_groups(tmp_path: Path) -> None:
    adapter = ExternalToolAdapter("trivy", "security", [], parse_trivy)
    payload = {
        "Results": [
            {
                "Target": "package-lock.json",
                "Vulnerabilities": [
                    {"VulnerabilityID": "CVE-1", "Title": "bad package", "Severity": "CRITICAL"}
                ],
                "Secrets": [{"RuleID": "key", "Title": "exposed key", "Severity": "HIGH"}],
            }
        ]
    }
    findings = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)
    assert [finding.category for finding in findings] == ["dependency-vulnerability", "secret"]
    assert findings[0].severity == "critical"


def test_checkov_parser_normalizes_failed_checks(tmp_path: Path) -> None:
    adapter = ExternalToolAdapter("checkov", "iac", [], parse_checkov)
    payload = {
        "results": {
            "failed_checks": [
                {
                    "check_id": "CKV_1",
                    "check_name": "Public resource",
                    "file_path": "/main.tf",
                    "file_line_range": [4, 8],
                }
            ]
        }
    }
    finding = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)[0]
    assert finding.file == "main.tf"
    assert finding.range is not None
    assert finding.range.start_line == 4
