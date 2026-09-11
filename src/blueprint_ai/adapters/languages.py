from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageAdapter:
    language: str
    markers: tuple[str, ...]
    quality: tuple[str, ...]
    test: tuple[str, ...]
    notes: str


LANGUAGE_ADAPTERS: dict[str, LanguageAdapter] = {
    "Python": LanguageAdapter(
        "Python",
        ("pyproject.toml", "requirements.txt"),
        ("ruff", "mypy"),
        ("pytest",),
        "Ruff is the fast default; configured mypy remains the native project type authority.",
    ),
    "JavaScript": LanguageAdapter(
        "JavaScript",
        ("package.json",),
        ("eslint", "biome"),
        ("npm-test",),
        "Use the repository-selected linter and package test script; never install implicitly.",
    ),
    "TypeScript": LanguageAdapter(
        "TypeScript",
        ("package.json", "tsconfig.json"),
        ("tsc", "eslint", "biome"),
        ("npm-test",),
        "tsc --noEmit is the type authority; ESLint/Biome are selected only when configured.",
    ),
    "Go": LanguageAdapter(
        "Go",
        ("go.mod",),
        ("gofmt", "go-vet"),
        ("go-test",),
        "The Go toolchain is the default authority for formatting, vetting, and tests.",
    ),
    "Rust": LanguageAdapter(
        "Rust",
        ("Cargo.toml",),
        ("cargo-fmt", "cargo-clippy"),
        ("cargo-test",),
        "Cargo fmt, Clippy, and cargo test are the Rust-native authorities.",
    ),
    "Java": LanguageAdapter(
        "Java",
        ("pom.xml", "build.gradle", "build.gradle.kts"),
        ("maven-check", "gradle-check"),
        ("maven-test", "gradle-test"),
        "Use the existing Maven/Gradle lifecycle so configured Checkstyle/SpotBugs "
        "remain authoritative.",
    ),
    "Shell": LanguageAdapter(
        "Shell",
        ("*.sh",),
        ("shellcheck",),
        ("bats",),
        "ShellCheck is the deterministic default; Bats runs only when the project already uses it.",
    ),
}


def language_adapter_registry() -> dict[str, LanguageAdapter]:
    return dict(LANGUAGE_ADAPTERS)
