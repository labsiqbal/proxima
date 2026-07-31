from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) < 5:
        return 2
    Path(sys.argv[1]).write_text("0", encoding="ascii")
    ready_fd = int(sys.argv[2])
    os.write(ready_fd, b"1")
    os.close(ready_fd)
    release_fd = int(sys.argv[3])
    released = os.read(release_fd, 1)
    os.close(release_fd)
    if released != b"1":
        return 3
    os.execvpe(sys.argv[4], sys.argv[4:], os.environ)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
