from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .controller import SafeUpdateController
from .recovery import format_status


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proxima-safe-updater")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("recovery-status")
    status.add_argument("--root", type=Path, required=True)
    status.add_argument("--run-id", required=True)
    status.add_argument("--intent-file", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        intent = json.loads(args.intent_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid intent file: {exc}") from exc
    if not isinstance(intent, dict):
        raise SystemExit("invalid intent file: expected a JSON object")
    value = SafeUpdateController(args.root).recovery_status(args.run_id, intent)
    print(format_status(args.run_id, value))
    return 0 if value.safe else 2


if __name__ == "__main__":
    raise SystemExit(main())
