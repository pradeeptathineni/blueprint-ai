import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_stable_1x_contract_matches_golden_fixture() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "benchmarks/compatibility_snapshot.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
