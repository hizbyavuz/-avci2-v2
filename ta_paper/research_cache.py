"""Reuse only explicitly verified, equivalent completed August research."""
import hashlib
import json
from pathlib import Path

RESEARCH_FILES = {'core.py': '271853954efb872b0ef3121f077a0417fbe286a6c3bf794b6e3e600bd4406218', 'archive.py': '77bc128002601a4f3bea6c4c742073f262a4a4ae8d81dad83da0a6b05f51c927', 'replay.py': 'e8a77e6191bc99f2da972729eb887feaa2641b793127a74bdac3df7bc96730e0', 'observations.py': 'd481c826d641a21a82199aeff813de2ebd86cb59ecf1a2dd7c2e780c6382b5a9'}
PROTOCOL_SHA = '58fa82481bd1d2ffe4a3520fe5956eea090915f6e281b8d8b6ca869ca29f3788'
VERIFIED_SOURCES = ('f66d9380a5cb83be94852939a9ca9f5607c21070f4b9ba8d991cde1f5ae51ec8',
                    'c8866b4e500e67555f663d2129d21ed4f10e03aa30be61b77a7dc599b4992477')


def completed_folder(root, source_sha, config_hash):
    root=Path(root);current=root/source_sha[:16]
    code=Path(__file__).resolve().parent
    protocol=code.parent/'research/ta_paper_v1_1/protocol.json'
    try:
        if (hashlib.sha256(protocol.read_bytes()).hexdigest()!=PROTOCOL_SHA or
                any(hashlib.sha256((code/name).read_bytes()).hexdigest()!=digest for name,digest in RESEARCH_FILES.items())):
            return current
        for sha in (source_sha,*VERIFIED_SOURCES):
            folder=root/sha[:16]
            state=json.loads((folder/'job_status.json').read_text()) if (folder/'job_status.json').exists() else {}
            if state.get('status') not in ('COMPLETE','COMPLETE_WITH_GAPS'):continue
            if state.get('provenance',{}).get('source_sha256')!=sha:continue
            report=json.loads((folder/'report.json').read_text())
            if (report.get('config_hash')==config_hash and
                    report.get('run_identity',{}).get('protocol_sha256')==PROTOCOL_SHA and
                    report.get('provenance',{}).get('source_sha256')==sha):
                return folder
    except (OSError,ValueError,TypeError):
        pass  # Unverifiable reports are never silently reused.
    return current
