import json
from pathlib import Path

from blueprint_ai.adapters.base import (
    CommandResult,
    ExternalToolAdapter,
    parse_checkov,
    parse_markdownlint,
    parse_osv,
    parse_ruff,
    parse_shellcheck,
    parse_terraform,
    parse_tflint,
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


def test_terraform_parser_marks_missing_initialization_as_incomplete(tmp_path: Path) -> None:
    adapter = ExternalToolAdapter("terraform", "iac", [], parse_terraform)
    payload = {
        "valid": False,
        "diagnostics": [
            {
                "severity": "error",
                "summary": "Module not installed",
                "detail": 'Run "terraform init" to install this module.',
                "range": {"filename": "main.tf", "start": {"line": 2}},
            }
        ],
    }
    finding = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)[0]
    assert finding.tool_metadata["incomplete_analysis"] is True


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
    assert finding.severity == "medium"
    assert finding.tool_metadata["original_severity"] == "unknown"
    assert finding.range is not None
    assert finding.range.start_line == 4


def test_checkov_discards_port_80_false_positive_for_security_group_reference(
    tmp_path: Path,
) -> None:
    adapter = ExternalToolAdapter("checkov", "iac", [], parse_checkov)
    payload = {
        "results": {
            "failed_checks": [
                {
                    "check_id": "CKV_AWS_260",
                    "check_name": "Unrestricted port 80",
                    "file_path": "/security.tf",
                    "file_line_range": [4, 12],
                    "code_block": [
                        [4, 'resource "aws_vpc_security_group_ingress_rule" "from_alb" {'],
                        [5, "referenced_security_group_id = aws_security_group.alb.id"],
                    ],
                }
            ]
        }
    }
    findings = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)
    assert findings == []


def test_shellcheck_parser_preserves_file_and_native_level(tmp_path: Path) -> None:
    adapter = ExternalToolAdapter("shellcheck", "code-quality", [], parse_shellcheck)
    payload = [
        {
            "file": "scripts/check.sh",
            "line": 7,
            "code": 2016,
            "level": "info",
            "message": "Expressions do not expand in single quotes",
        }
    ]
    finding = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)[0]
    assert finding.file == "scripts/check.sh"
    assert finding.severity == "info"
    assert finding.range and finding.range.start_line == 7


def test_shellcheck_parser_aggregates_same_rule_per_file(tmp_path: Path) -> None:
    adapter = ExternalToolAdapter("shellcheck", "code-quality", [], parse_shellcheck)
    payload = [
        {"file": "script.sh", "line": line, "code": 2016, "level": "info", "message": "quoted"}
        for line in (7, 12)
    ]
    finding = adapter.parse(CommandResult([], 1, json.dumps(payload), ""), tmp_path)[0]
    assert finding.tool_metadata["occurrences"] == 2
    assert finding.evidence == ["also at line 12"]


def test_tflint_parser_reads_issue_schema(tmp_path: Path) -> None:
    adapter = ExternalToolAdapter("tflint", "iac", [], parse_tflint)
    payload = {
        "issues": [
            {
                "rule": {
                    "name": "terraform_required_version",
                    "severity": "warning",
                    "link": "https://example.test/rule",
                },
                "message": "required_version is required",
                "range": {
                    "filename": "modules/web/versions.tf",
                    "start": {"line": 7, "column": 1},
                },
            }
        ],
        "errors": [],
    }
    finding = adapter.parse(CommandResult([], 2, json.dumps(payload), ""), tmp_path)[0]
    assert finding.rule_id == "tflint/terraform_required_version"
    assert finding.file == "modules/web/versions.tf"
    assert finding.range and finding.range.start_line == 7


def test_markdownlint_parser_aggregates_same_rule_per_file(tmp_path: Path) -> None:
    adapter = ExternalToolAdapter("markdownlint-cli2", "documentation", [], parse_markdownlint)
    output = (
        "README.md:3:81 error MD013/line-length Line length [Actual: 90]\n"
        "README.md:8:81 error MD013/line-length Line length [Actual: 95]\n"
    )
    finding = adapter.parse(CommandResult([], 1, output, ""), tmp_path)[0]
    assert finding.rule_id == "markdownlint/MD013"
    assert finding.priority == "P3"
    assert finding.tool_metadata["occurrences"] == 2
    assert finding.evidence == ["also at line 8"]
