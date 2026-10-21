"""Create a private recovery snapshot through the package command."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from background_task_core.store import backup_main

if __name__ == '__main__':
    raise SystemExit(backup_main(['registration', *sys.argv[1:]]))
