"""Cold wheels must retain consumer-owned PRIVATE admission outside the source checkout."""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def run(arguments, **kwargs):
    execution = subprocess.run(arguments, capture_output=True, text=True, timeout=180, **kwargs)
    assert execution.returncode == 0, execution.stdout + execution.stderr
    return execution


def test_installed_observation_binding_rejects_its_package_directory(tmp_path):
    spec = importlib.util.spec_from_file_location("console_fixtures", ROOT / "tools/make_fixtures.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    binding = generator.observation_binding_case(tmp_path)
    # CI installs the clean console wheel and pinned dependencies before this probe.
    # Isolated mode must exercise that installation, without source-path assistance.
    probe = """
import json, pathlib, sys
from task_console import observations
package = pathlib.Path(observations.__file__).resolve().parent
assert package.is_relative_to(pathlib.Path(sys.prefix))
assert not package.is_relative_to(pathlib.Path(sys.argv[2]))
path = pathlib.Path(sys.argv[1])
binding = observations.load_binding(path)
assert not pathlib.Path(binding['snapshot']).exists()
binding.update(private_dir=str(package), snapshot=str(package / 'synthetic-snapshot.json'))
path.write_text(json.dumps(binding), encoding='utf-8')
try:
    observations.load_binding(path)
except RuntimeError as error:
    assert 'INSIDE its own repo' in str(error), str(error)
else:
    raise AssertionError('installed observation output inside its own package was admitted')
assert not pathlib.Path(binding['snapshot']).exists()
print('installed observation binding: external accepted; package refused')
"""
    output = run([sys.executable, "-I", "-B", "-c", probe, str(binding), str(ROOT)], cwd=tmp_path)
    assert output.stdout.strip() == "installed observation binding: external accepted; package refused"


def test_cold_wheel_preserves_private_admission_and_requires_its_own_contract(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for filename in ("pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "LICENSE", "storage.contract.json"):
        shutil.copyfile(ROOT / filename, source / filename)
    shutil.copytree(ROOT / "scripts/task_console", source / "scripts/task_console",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    guards = tmp_path / "guards-source"
    guards.mkdir()
    for filename in ("pyproject.toml", "setup.py", "PACKAGE.md", "LICENSE"):
        shutil.copyfile(ROOT / "guards" / filename, guards / filename)
    shutil.copytree(ROOT / "guards/fleet_guards", guards / "fleet_guards",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (guards / "tools").mkdir()
    for filename in ("datadir.py", "data_boundary.py", "pii_guard.py", "storage_contract.py"):
        shutil.copyfile(ROOT / "guards/tools" / filename, guards / "tools" / filename)
    wheels = tmp_path / "wheels"
    for package in (guards, source):
        run([sys.executable, "-m", "pip", "wheel", str(package), "--no-deps", "--no-build-isolation",
             "--wheel-dir", str(wheels)], cwd=tmp_path)
    wheel, = wheels.glob("task_console-*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("task_console/storage.contract.json") == (ROOT / "storage.contract.json").read_bytes()
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        assert "Requires-Dist: fleet-guards>=0.2.1" in archive.read(metadata).decode()
    environment = tmp_path / "venv"
    run([sys.executable, "-m", "venv", str(environment)], cwd=tmp_path)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([str(python), "-m", "pip", "install", "--no-deps", *map(str, wheels.glob("*.whl"))], cwd=tmp_path)

    spec = importlib.util.spec_from_file_location("installed_console_fixtures", ROOT / "guards/tools/make_fixtures.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    layout = generator.make_storage_contract_fixture(
        tmp_path / "storage", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    home = tmp_path / "home"
    (home / ".pii-guard").mkdir(parents=True)
    shutil.copyfile(layout["receipt"], home / ".pii-guard/visibility.json")
    env = {name: value for name, value in layout["companion"].env.items()
           if not name.startswith("TASK_CONSOLE_")}
    env.update(HOME=str(home), USERPROFILE=str(home), TASK_CONSOLE_CONFIG=str(layout["companion"].root))
    probe = """
import json, pathlib, sys
from task_console import console_store as store
package = pathlib.Path(store.__file__).parent
assert package.is_relative_to(pathlib.Path(sys.prefix))
assert store.SOURCE_ROOT == package
path, state = store.resolve_db(create_parent=True)
expected = sys.argv[1]
if expected == 'private':
    assert state is None and path.parent.is_dir() and not path.exists(), state.as_dict() if state else None
elif expected == 'missing-contract':
    contract = package / 'storage.contract.json'
    contract.rename(contract.with_suffix('.held'))
    path, state = store.resolve_db(create_parent=True)
    assert path is None and state is not None
else:
    assert path is None and state is not None
print(json.dumps({'case': expected, 'status': 'refused' if state else 'admitted'}))
"""
    for route in ("private", "public", "unknown", "missing-contract"):
        destination = "private" if route == "missing-contract" else route
        layout["companion"].git("config", "remote.origin.pushurl",
                                "https://github.com/example-owner/synthetic-" + destination + ".git")
        output = run([str(python), "-I", "-B", "-c", probe, route], cwd=tmp_path, env=env)
        expected = "admitted" if route == "private" else "refused"
        assert json.loads(output.stdout) == {"case": route, "status": expected}
