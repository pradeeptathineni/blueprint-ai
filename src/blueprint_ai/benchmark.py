from __future__ import annotations

import statistics
import time
import tracemalloc
from pathlib import Path

from pydantic import BaseModel

from blueprint_ai.discovery import discover_project
from blueprint_ai.model.context import ContextBuilder


class BenchmarkResult(BaseModel):
    schema_version: int = 1
    path: str
    repeats: int
    file_count: int
    discovery_ms_median: float
    context_ms_median: float
    peak_memory_bytes: int
    files_considered: int
    files_read: int
    bytes_read: int
    estimated_model_input_tokens: int
    model_calls: int = 0
    cache_hit_rate: float | None = None
    findings_count: int | None = None


def benchmark_repository(root: Path, repeats: int = 3) -> BenchmarkResult:
    root = root.resolve()
    discovery_times = []
    context_times = []
    facts = discover_project(root)
    context = ""
    metrics: dict[str, int] = {}
    cache_hit_rate: float | None = None
    for _ in range(repeats):
        started = time.perf_counter()
        facts = discover_project(root)
        discovery_times.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        builder = ContextBuilder(root, token_budget=12_000)
        context = builder.build("architecture", facts, [])
        context_times.append((time.perf_counter() - started) * 1000)
        metrics = builder.metrics
        builder.build("architecture", facts, [])
        cached = builder.metrics
        if cached.get("files_read"):
            cache_hit_rate = 1 - cached.get("new_files_read", 0) / cached["files_read"]
    tracemalloc.start()
    measured_facts = discover_project(root)
    measured_builder = ContextBuilder(root, token_budget=12_000)
    measured_builder.build("architecture", measured_facts, [])
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return BenchmarkResult(
        path=str(root),
        repeats=repeats,
        file_count=facts.file_count,
        discovery_ms_median=round(statistics.median(discovery_times), 3),
        context_ms_median=round(statistics.median(context_times), 3),
        peak_memory_bytes=peak,
        files_considered=metrics.get("files_considered", 0),
        files_read=metrics.get("files_read", 0),
        bytes_read=metrics.get("bytes_read", 0),
        estimated_model_input_tokens=(len(context) + 3) // 4,
        cache_hit_rate=cache_hit_rate,
    )
