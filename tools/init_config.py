"""Create a reviewable native PowerShell environment template in PRIVATE storage."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/task_console"))
import console_store


def initialize(root):
    root = Path(root).expanduser().absolute()
    path = root / "settings.ps1"
    console_store.authorize_write(path, artifact_id="native-settings")
    if path.exists():
        return path, False
    content = ("# Review and dot-source this file in the console process environment.\n"
               "$env:TASK_CONSOLE_CONFIG = $PSScriptRoot\n"
               "$env:TASK_CONSOLE_DB = Join-Path $PSScriptRoot 'data/task-console/console.sqlite3'\n"
               "$env:TASK_CONSOLE_READ_ONLY = '1'\n"
               "# Optional resource paths stay unset until explicitly configured.\n")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return path, True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        root = args.out or console_store.companion_root()
        if root is None:
            raise ValueError("Select an initialized PRIVATE companion with --out or TASK_CONSOLE_CONFIG")
        path, created = initialize(root)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print("NOT READY: " + str(error))
        return 1
    print(("Created template: " if created else "Preserved existing settings: ") + str(path))
    print("Review, dot-source, and run tools/verify_config.py. This command creates no database or task.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
