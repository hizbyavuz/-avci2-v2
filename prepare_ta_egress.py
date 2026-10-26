"""Prepare local relay secrets/state; never provisions a host or contacts Binance."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess

from ta_paper.rate_limit import RequestGate


def prepare(root, address, cooldown_file, uid=10001, gid=10001):
    address = str(ipaddress.IPv4Address(address))
    # Validate the actual exported cooldown, not a guessed/default expiry.
    value = json.loads(Path(cooldown_file).read_text())
    if type(value.get('until_ms')) is not int or value['until_ms'] < 0:
        raise ValueError('COOLDOWN_INVALID')
    if not isinstance(value.get('reason'), str) or not isinstance(value.get('source'), str):
        raise ValueError('COOLDOWN_INVALID')
    RequestGate(cooldown_file)
    root = Path(root)
    # Never overwrite TLS keys, tokens or an existing durable block.
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    old_mask = os.umask(0o077)
    try:
        state = root/'state'
        tls = root/'tls'
        state.mkdir()
        tls.mkdir()
        (state/'cooldown.json').write_text(json.dumps(value, sort_keys=True)+'\n')
        (root/'egress.env').write_text('TA_EGRESS_TOKEN='+secrets.token_hex(32)+'\n')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes',
                        '-keyout', str(tls/'server.key'), '-out', str(tls/'server.crt'),
                        '-days', '3650', '-subj', '/CN=TA-Paper-Private-Egress',
                        '-addext', 'subjectAltName=IP:'+address,
                        '-addext', 'keyUsage=critical,digitalSignature,keyEncipherment',
                        '-addext', 'extendedKeyUsage=serverAuth'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for folder in (state, tls):
            os.chown(folder, uid, gid)
            for file in folder.iterdir():
                os.chown(file, uid, gid)
        # Docker mounts subdirectories before dropping privileges. The parent
        # and env file remain root-only; the relay cannot read the host token file.
    finally:
        os.umask(old_mask)
    return {'status': 'PREPARED_NOT_STARTED', 'cooldown_until_ms': value['until_ms']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--address', required=True)
    parser.add_argument('--cooldown-file', required=True)
    parser.add_argument('--root', default='/opt/ta-egress')
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.address, args.cooldown_file)))
