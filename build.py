"""Build a reproducible source zipapp using only the Python standard library."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parent


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("only regular source files may be packaged")
    return path.read_bytes()


def members():
    provenance = regular(ROOT / "core-sources.json")
    pins = json.loads(provenance)
    entries = {"__main__.py": b"from dreamm.cli import main\nraise SystemExit(main())\n",
               "LICENSE": regular(ROOT / "LICENSE"), "core-sources.json": provenance}
    for package in ("dreamm", "dreamm_m1", "dreamm_ensemble"):
        folder = ROOT / "src" / package
        if folder.is_symlink():
            raise ValueError("linked package directory")
        for path in sorted(folder.glob("*.py")):
            raw = regular(path)
            ast.parse(raw, filename=str(path))
            entries[f"{package}/{path.name}"] = raw
    entries["dreamm/data/compatibility-profile.json"] = regular(ROOT / "src/dreamm/data/compatibility-profile.json")
    entries["dreamm/data/protdcal-schema.json"] = regular(ROOT / "src/dreamm/data/protdcal-schema.json")
    entries["dreamm/data/p2rank251.json"] = regular(ROOT / "src/dreamm/data/p2rank251.json")
    entries["dreamm/data/exprose_entry.jl"] = regular(ROOT / "src/dreamm/data/exprose_entry.jl")
    for path, expected in pins["files"].items():
        if path not in entries or sha(entries[path]) != expected:
            raise ValueError("scientific source differs from exported core: " + path)
    protected = {p for p in entries if p.startswith(("dreamm_m1/", "dreamm_ensemble/"))}
    if protected != {p for p in pins["files"] if p.endswith(".py")}:
        raise ValueError("scientific module inventory differs")
    return entries


def build(output):
    entries = members()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, raw in sorted(entries.items()):
                item = zipfile.ZipInfo(name, (2026, 11, 13, 0, 0, 0))
                item.create_system = 3
                item.external_attr = (stat.S_IFREG | 0o644) << 16
                item.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(item, raw, compresslevel=9)
    return {"artifact": str(output), "sha256": sha(output.read_bytes()),
            "bytes": output.stat().st_size, "members": len(entries)}


if __name__ == "__main__":
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--output", default="dist/dreamm.pyz")
    options = args.parse_args()
    print(json.dumps(build(options.output), indent=2))
