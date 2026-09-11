# Performance fixtures

Run `python benchmarks/run.py` from the repository root. The script creates disposable Git projects
with 100, 1,000, and 5,000 source files, then records discovery/context time, peak traced memory,
files and bytes read, and estimated model tokens. Results are machine-readable and intentionally not
committed as universal thresholds because filesystem and hardware differences dominate absolute
time. Pull requests should compare the same host before and after a change.
