"""Compute (or write) the next semantic version from Conventional Commit messages.

Usage in CI:
  git log --format='%s%n%b%n--commit--' <range> | python next_version.py <last_tag>
      -> prints "release=true|false", "version=X.Y.Z" and "bump=..." for $GITHUB_OUTPUT
  python next_version.py --write X.Y.Z
      -> updates the version in pyproject.toml

Rules (since the last vX.Y.Z tag):
  "type!:" or "BREAKING CHANGE"            -> major
  feat:                                     -> minor
  fix: perf: refactor: revert: deps:        -> patch
  docs: chore: ci: test: style: build:      -> no release
With no previous tag, the version already in pyproject.toml is released as is.
"""

import pathlib
import re
import sys

PYPROJECT = pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"
VERSION_RE = re.compile(r'^(version\s*=\s*")(\d+)\.(\d+)\.(\d+)(")', re.M)
TYPE_RE = re.compile(r"^(?P<type>[a-z]+)(\([^)]*\))?(?P<bang>!)?:\s", re.I)
PATCH_TYPES = {"fix", "perf", "refactor", "revert", "deps"}


def current_version() -> tuple[int, int, int]:
    m = VERSION_RE.search(PYPROJECT.read_text(encoding="utf-8"))
    if not m:
        sys.exit("No encontré version = \"X.Y.Z\" en pyproject.toml")
    return int(m.group(2)), int(m.group(3)), int(m.group(4))


def write_version(version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    major, minor, patch = version.split(".")
    new = VERSION_RE.sub(lambda m: f"{m.group(1)}{major}.{minor}.{patch}{m.group(5)}", text, count=1)
    PYPROJECT.write_text(new, encoding="utf-8")


def bump_from(messages: list[str]) -> str | None:
    level = None
    for msg in messages:
        subject = msg.strip().splitlines()[0] if msg.strip() else ""
        if not subject or subject.lower().startswith("chore(release)"):
            continue
        m = TYPE_RE.match(subject)
        if (m and m.group("bang")) or "BREAKING CHANGE" in msg:
            return "major"
        kind = m.group("type").lower() if m else ""
        if kind == "feat":
            level = "minor"
        elif kind in PATCH_TYPES and level is None:
            level = "patch"
    return level


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--write":
        write_version(sys.argv[2])
        return

    last_tag = sys.argv[1] if len(sys.argv) > 1 else ""
    major, minor, patch = current_version()

    if not last_tag:
        print("release=true")
        print(f"version={major}.{minor}.{patch}")
        print("bump=initial")
        return

    tagged = tuple(int(x) for x in last_tag.lstrip("v").split("."))
    base = max(tagged, (major, minor, patch))
    messages = [m for m in sys.stdin.read().split("--commit--") if m.strip()]
    bump = bump_from(messages)
    if bump is None:
        print("release=false")
        print(f"version={'.'.join(map(str, base))}")
        print("bump=none")
        return

    major, minor, patch = base
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    print("release=true")
    print(f"version={major}.{minor}.{patch}")
    print(f"bump={bump}")


if __name__ == "__main__":
    main()
