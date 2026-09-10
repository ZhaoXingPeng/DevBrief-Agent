from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")


def main() -> int:
    failures: list[str] = []
    for document in ROOT.rglob("*.md"):
        if any(part in {".git", "node_modules"} for part in document.parts):
            continue
        for target in LINK.findall(document.read_text(encoding="utf-8")):
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative = unquote(target.split("#", maxsplit=1)[0])
            if not relative:
                continue
            candidate = (document.parent / relative).resolve()
            if candidate != ROOT and ROOT not in candidate.parents:
                failures.append(
                    f"{document.relative_to(ROOT)}: escapes repository: {target}"
                )
            elif not candidate.exists():
                failures.append(
                    f"{document.relative_to(ROOT)}: missing target: {target}"
                )
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("Markdown local links: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
