"""Check tracked files without printing possible secret content."""

import re
import subprocess
from pathlib import Path

SECRET_ASSIGNMENT = re.compile(
    rb"(?m)^[ \t]*(?:[A-Z0-9_]*(?:API_KEY|API_SECRET|PRIVATE_KEY|PASSPHRASE|TOKEN))"
    rb"[ \t]*=[ \t]*['\"]?([^ \t\r\n'\"#]+)"
)
PRIVATE_KEY_BLOCK = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
SECRET_JSON_ASSIGNMENT = re.compile(
    rb"(?m)^[ \t]*[\"'](?:[A-Z0-9_]*(?:API_KEY|API_SECRET|PRIVATE_KEY|PASSPHRASE|TOKEN))"
    rb"[\"'][ \t]*:[ \t]*[\"']([^\"'\r\n]+)"
)


def violations(name: str, data: bytes) -> tuple[str, ...]:
    path = Path(name)
    issues: list[str] = []
    if path.name == ".env" or (
        path.name.startswith(".env.") and path.name != ".env.example"
    ):
        issues.append("tracked_env_file")
    if path.suffix.lower() in (".pem", ".p12", ".pfx"):
        issues.append("tracked_private_key_file")
    if PRIVATE_KEY_BLOCK.search(data):
        issues.append("private_key_block")
    if SECRET_ASSIGNMENT.search(data) or SECRET_JSON_ASSIGNMENT.search(data):
        issues.append("nonempty_secret_assignment")
    return tuple(issues)


def main() -> int:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], check=True, capture_output=True
    ).stdout.split(b"\0")
    failures: list[tuple[str, str]] = []
    for raw_name in tracked:
        if not raw_name:
            continue
        name = raw_name.decode("utf-8", errors="surrogateescape")
        path = Path(name)
        if not path.is_file():
            continue
        for issue in violations(name, path.read_bytes()):
            failures.append((name, issue))
    for name, issue in failures:
        print(f"secret hygiene: {name}: {issue}")
    if failures:
        return 1
    print("secret hygiene: tracked files passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
