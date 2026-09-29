"""Small CI guard for obvious accidental secret commits; not a full secret scanner."""
import re
import subprocess
from pathlib import Path

tracked = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
if any(Path(name).name == ".env" for name in tracked):
    raise SystemExit("A .env file is tracked")
patterns = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{25,}"),
    re.compile(r"(?:OPENAI|ANTHROPIC|GEMINI)_API_KEY[ \t]*=[ \t]*[^\s#]+"),
]
for name in tracked:
    path = Path(name)
    if not path.is_file() or path.stat().st_size > 1_000_000:
        continue
    content = path.read_text(encoding="utf-8", errors="ignore")
    for pattern in patterns:
        if pattern.search(content):
            raise SystemExit(f"Possible secret in {name}")
print("No obvious tracked secrets found")
