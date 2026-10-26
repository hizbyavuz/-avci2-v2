"""Optional single private egress; configuration errors never fall back to Railway."""
import os
import re
import ssl
import urllib.parse
import urllib.request


class MarketTransport:
    def __init__(self, *, direct=False, environ=None):
        env = {} if direct else (os.environ if environ is None else environ)
        url = env.get('TA_PAPER_EGRESS_URL', '')
        token = env.get('TA_PAPER_EGRESS_TOKEN', '')
        certificate = env.get('TA_PAPER_EGRESS_CA_PEM', '')
        self.base = 'https://fapi.binance.com'
        self.token = None
        self.context = None
        self.mode = 'DIRECT'
        if not any((url, token, certificate)):
            return
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.path not in ('', '/') or parsed.query or
                parsed.fragment or not re.fullmatch(r'[a-f0-9]{64}', token)):
            raise ValueError('EGRESS_CONFIGURATION_INVALID')
        if not certificate:
            raise ValueError('EGRESS_CERTIFICATE_REQUIRED')
        # Trust only the provisioned certificate, with hostname/IP verification.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cadata=certificate)
        self.base = url.rstrip('/')
        self.token = token
        self.context = context
        self.mode = 'PRIVATE_EGRESS'

    def request(self, endpoint, params):
        headers = {'User-Agent': 'TA-Paper-Research/1', 'Accept-Encoding': 'gzip'}
        if self.token:
            headers['Authorization'] = 'Bearer '+self.token
        return urllib.request.Request(self.base+endpoint+'?'+urllib.parse.urlencode(params),
                                      headers=headers, method='GET')

    def handlers(self):
        # Ignore ambient HTTP(S)_PROXY values: the configured route is exclusive.
        result = [urllib.request.ProxyHandler({})]
        if self.context:
            result.append(urllib.request.HTTPSHandler(context=self.context))
        return result
