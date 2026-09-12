from __future__ import annotations

import json
import tempfile
from pathlib import Path

from blueprint_ai.benchmark import benchmark_repository


def main() -> None:
    results = []
    with tempfile.TemporaryDirectory(prefix="blueprint-ai-benchmark-") as temporary:
        root = Path(temporary)
        for count in (100, 1_000, 5_000):
            project = root / str(count)
            source = project / "src"
            source.mkdir(parents=True)
            (project / "pyproject.toml").write_text(
                f'[project]\nname = "bench-{count}"\nversion = "1"\n'
            )
            for index in range(count):
                (source / f"module_{index:05}.py").write_text(
                    f"def value_{index}():\n    return {index}\n"
                )
            results.append(benchmark_repository(project, repeats=3).model_dump(mode="json"))
    for result in results:
        assert result["files_read"] <= 24 and result["estimated_model_input_tokens"] <= 12_000
        assert result["peak_memory_bytes"] <= 64 * 1024 * 1024 and result["model_calls"] == 0
    print(json.dumps({"schema_version": 1, "results": results}, indent=2))


if __name__ == "__main__":
    main()
