"""Include the source-owned storage policy in built wheels without a second source copy."""
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildStoragePolicy(build_py):
    def run(self):
        super().run()
        source = Path(__file__).parent / "storage.contract.json"
        destination = Path(self.build_lib) / "task_console/storage.contract.json"
        self.copy_file(str(source), str(destination))


setup(cmdclass={"build_py": BuildStoragePolicy})
