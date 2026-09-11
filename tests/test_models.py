from blueprint_ai.core import Finding
from blueprint_ai.core.priority import deduplicate


def test_fingerprint_is_stable_and_source_independent() -> None:
    first = Finding(
        blueprint="security",
        category="secret",
        source="a",
        message="Found secret",
        recommendation="remove",
    )
    second = Finding(
        blueprint="security",
        category="secret",
        source="b",
        message="  found  SECRET ",
        recommendation="remove",
    )
    assert first.fingerprint == second.fingerprint


def test_dedupe_preserves_sources_and_highest_priority() -> None:
    first = Finding(
        blueprint="security",
        category="secret",
        source="gitleaks",
        severity="medium",
        file="a.py",
        message="CVE-2026-1000 affects demo",
        recommendation="remove",
    )
    second = Finding(
        blueprint="security",
        category="secret",
        source="trivy",
        severity="high",
        file="a.py",
        message="CVE-2026-1000: vulnerable demo",
        recommendation="remove",
    )
    result = deduplicate([first, second], ["api"])
    assert len(result) == 1
    assert result[0].priority == "P1"
    assert "also reported by trivy" in result[0].evidence


def test_dedupe_keeps_distinct_findings_on_same_file_and_line() -> None:
    findings = [
        Finding(
            blueprint="security",
            category="dependency-vulnerability",
            source="osv",
            severity="high",
            file="lock.json",
            message=f"CVE-2026-{number} affects demo",
            recommendation="upgrade",
        )
        for number in (1, 2)
    ]
    assert len(deduplicate(findings, [])) == 2
