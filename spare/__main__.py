import sys

from .cli import main

# Windows writes to a pipe or file in its legacy code page (cp1252), which can't encode characters
# such as ≥ or × in our output. The console itself is already UTF-8, so only switch when needed.
for stream in (sys.stdout, sys.stderr):
    if stream is not None and (stream.encoding or "").lower().replace("-", "") != "utf8":
        stream.reconfigure(encoding="utf-8", errors="replace")

sys.exit(main())
