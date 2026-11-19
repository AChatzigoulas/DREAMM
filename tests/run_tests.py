"""Run only the lightweight standalone tests and optionally retain a JSON receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
args = argparse.ArgumentParser(description=__doc__)
args.add_argument("--report")
options = args.parse_args()
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
record = {"scope": "standalone_transport_runtime_integrity_and_fake_native_boundaries_no_scientific_execution",
          "python": platform.python_version(), "platform": platform.platform(), "tests_run": result.testsRun,
          "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
          "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
          "source_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted((ROOT / "src/dreamm").glob("*.py"))}}
if options.report:
    Path(options.report).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record, indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)
