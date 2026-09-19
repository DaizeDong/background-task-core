"""Start the bundled HTTP server using only the selected installed package."""
from pathlib import Path
import runpy
import sys


def main():
    directory = Path(__file__).resolve().parent
    sys.path.insert(0, str(directory))
    runpy.run_path(str(directory / 'server.py'), run_name='__main__')


if __name__ == '__main__':
    main()
