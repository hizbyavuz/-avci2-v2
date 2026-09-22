"""Restore validated GitHub Actions research state and preserve daily checkpoints."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path


def gh_json(endpoint):
    result = subprocess.run(["gh", "api", endpoint], check=True,
                            capture_output=True, text=True)
    return json.loads(result.stdout)


def artifacts(name):
    repo = os.environ["GITHUB_REPOSITORY"]
    response = gh_json(f"repos/{repo}/actions/artifacts?name={name}&per_page=100")
    return sorted((a for a in response["artifacts"]
                   if a["name"] == name and not a["expired"]),
                  key=lambda a: a["created_at"], reverse=True)


def download(artifact, destination):
    subprocess.run(["gh", "run", "download", str(artifact["workflow_run"]["id"]),
                    "--name", artifact["name"], "--dir", str(destination)],
                   check=True, capture_output=True, text=True)


def valid_database(path, expected_table):
    if not path.is_file():
        return False
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return False
            return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name=?", (expected_table,)).fetchone() is not None
    except sqlite3.DatabaseError:
        return False


def row_count(path, table):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def restore_binance():
    repo = os.environ["GITHUB_REPOSITORY"]
    # Daily copies survive pruning of the last-two fast restart copies.
    listing = gh_json(f"repos/{repo}/actions/artifacts?per_page=100")["artifacts"]
    candidates = sorted((a for a in listing if not a["expired"]
                         and (a["name"] == "binance-avci2-state"
                              or a["name"].startswith("binance-avci2-daily-"))),
                        key=lambda a: a["created_at"], reverse=True)
    checked = set()
    def attempt(artifact):
        checked.add(artifact["id"])
        with tempfile.TemporaryDirectory() as folder:
            try:
                download(artifact, folder)
                source = Path(folder) / "binance_avci2.db"
                if not valid_database(source, "scans"):
                    print(f"Skipping damaged Binance backup {artifact['name']} {artifact['id']}")
                    return False
                shutil.copyfile(source, "binance_avci2.db")
                print(f"Binance state restored: {artifact['name']} {artifact['created_at']}")
                return True
            except (subprocess.CalledProcessError, OSError) as error:
                print(f"Could not restore {artifact['name']}: {error}")
                return False
    for artifact in candidates:
        if attempt(artifact):
            return
    # Listing is capped at 100. Fetch older daily copies by their exact name.
    for day_offset in range(31):
        date = (datetime.now(timezone.utc) - timedelta(days=day_offset)).date()
        for artifact in artifacts(f"binance-avci2-daily-{date}"):
            if artifact["id"] not in checked and attempt(artifact):
                return
    raise RuntimeError("No valid Binance research backup; scan stopped")


def restore_gate():
    candidates = artifacts("gate-avci2-signal-history")
    if not candidates:
        for day_offset in range(31):
            date = (datetime.now(timezone.utc) - timedelta(days=day_offset)).date()
            candidates = artifacts(f"gate-avci2-daily-{date}")
            if candidates:
                break
    if candidates:
        with tempfile.TemporaryDirectory() as folder:
            download(candidates[0], folder)
            required = (("avci2.db", "snapshots"),
                        ("avci_outcomes.db", "signals"))
            for filename, table in required:
                source = Path(folder) / filename
                if not valid_database(source, table):
                    raise RuntimeError(f"Incomplete Gate backup: {filename}")
            validation = Path(folder) / "avci_validation_v5.db"
            if not validation.exists():
                print("WARNING: previous Gate runs did not back up V5 validation; "
                      "earlier validation history cannot be recovered")
            elif not valid_database(validation, "validation_events"):
                raise RuntimeError("Gate validation backup is damaged")
            checks = required + (("avci_validation_v5.db", "validation_events"),)
            cache_valid = all(valid_database(Path(filename), table)
                              for filename, table in checks)
            archive_valid = all(valid_database(Path(folder) / filename, table)
                                for filename, table in checks)
            if cache_valid:
                comparable = checks if archive_valid else required
                previous = [row_count(Path(filename), table)
                            for filename, table in comparable]
                archived = [row_count(Path(folder) / filename, table)
                            for filename, table in comparable]
                if all(a >= b for a, b in zip(previous, archived)):
                    print("Using newer complete Gate cache")
                    return
                if not all(b >= a for a, b in zip(previous, archived)):
                    raise RuntimeError("Gate backup histories conflict; refusing to discard events")
            for filename, _ in required:
                shutil.copyfile(Path(folder) / filename, filename)
            if validation.exists():
                shutil.copyfile(validation, validation.name)
            elif Path("avci_validation_v5.db").exists() and not valid_database(
                    Path("avci_validation_v5.db"), "validation_events"):
                raise RuntimeError("Gate validation cache is damaged")
        print(f"Gate state restored: {candidates[0]['created_at']}")
        return
    # Historical cache is a fallback for accounts with no prior artifact.
    if (Path("avci2.db").exists() or Path("avci_outcomes.db").exists()
            or Path("avci_validation_v5.db").exists()):
        if not (valid_database(Path("avci2.db"), "snapshots")
                and valid_database(Path("avci_outcomes.db"), "signals")
                and valid_database(Path("avci_validation_v5.db"),
                                   "validation_events")):
            raise RuntimeError("Partial Gate cache; refusing to lose historical outcomes")
        print("Gate state restored from previous cache")
    else:
        print("No Gate state yet; initial run")


def daily_needed():
    day = datetime.now(timezone.utc).date().isoformat()
    venue = sys.argv[2] if len(sys.argv) > 2 else "binance"
    if venue not in ("binance", "gate"):
        raise ValueError("Unknown venue")
    name = f"{venue}-avci2-daily-{day}"
    print(f"name={name}")
    print(f"needed={'false' if artifacts(name) else 'true'}")


if __name__ == "__main__":
    {"restore-binance": restore_binance,
     "restore-gate": restore_gate,
     "daily-needed": daily_needed}[sys.argv[1]]()
