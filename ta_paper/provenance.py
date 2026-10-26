"""Code identity is distinct from the strategy configuration identity."""
import hashlib
import os
from pathlib import Path
import re
import subprocess


def identity():
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for path in sorted(root.glob('*.py')):
        h.update(path.name.encode()+b'\0'+path.read_bytes()+b'\0')
    commit = os.getenv('TA_PAPER_COMMIT_SHA') or os.getenv('RAILWAY_GIT_COMMIT_SHA')
    dirty = None
    if not commit:
        try:
            commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,
                                             stderr=subprocess.DEVNULL,text=True,timeout=2).strip()
            dirty = bool(subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],
                                                cwd=root,stderr=subprocess.DEVNULL,text=True,timeout=2).strip())
        except (OSError,subprocess.SubprocessError):
            commit = 'UNKNOWN'
    if not re.fullmatch(r'[0-9a-fA-F]{40}',commit):
        commit = 'UNKNOWN'
    return dict(git_commit=commit,source_sha256=h.hexdigest(),working_tree_dirty=dirty)
