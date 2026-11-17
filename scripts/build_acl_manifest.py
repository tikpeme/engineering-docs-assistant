"""
Build acl_manifest.json for the fake S: drive.

Stands in for the PowerShell Get-Acl export a real Kestrel deployment would run
on the file server. The ingest worker joins this manifest with each file in S3
to set allowed_groups.

Usage (from the repo root):
    python scripts/build_acl_manifest.py
"""

import json
from pathlib import Path

import yaml

ROOT = Path("sample-s-drive")
CONFIG = Path("scripts/acl_config.yaml")
OUT = ROOT / "acl_manifest.json"


def groups_for(rel_path: str, folders: dict[str, list[str]]) -> list[str]:
    """Longest matching folder prefix wins; no match means no groups (default deny)."""
    matches = [f for f in folders if rel_path == f or rel_path.startswith(f + "/")]
    if not matches:
        return []
    return folders[max(matches, key=len)]


def main() -> None:
    folders = yaml.safe_load(CONFIG.read_text())["folders"]
    entries = []
    for f in sorted(ROOT.rglob("*")):
        if not f.is_file() or f == OUT:
            continue
        rel = f.relative_to(ROOT).as_posix()
        entries.append({"path": rel, "groups": groups_for(rel, folders)})

    OUT.write_text(json.dumps(entries, indent=2))
    print(f"Wrote {len(entries)} entries to {OUT}")
    for e in entries:
        print(f"  {e['path']:<70} {e['groups'] or 'NO GROUPS (deny)'}")


if __name__ == "__main__":
    main()
