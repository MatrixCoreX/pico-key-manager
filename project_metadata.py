"""Published build provenance and local device state are separate files."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_build_metadata():
    local = ROOT / "build-info.json"
    source = local if local.exists() else ROOT / "firmware/build-manifest.json"
    return json.loads(source.read_text())


def store_build_metadata(metadata):
    (ROOT / "build-info.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
