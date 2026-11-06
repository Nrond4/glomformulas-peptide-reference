"""Small bounded read client. Honors inherited proxy/TLS settings and host limits."""
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

AGENT = 'GloFormulasReference/0.2 (research catalog monitor)'

class FetchError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

@dataclass
class Response:
    url: str
    body: bytes
    headers: dict
    status: int = 200

    @property
    def text(self):
        return self.body.decode('utf-8-sig')

    def json(self):
        return json.loads(self.text)

class Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, check):
        self.check = check

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

class Client:
    def __init__(self, hosts, timeout=25, attempts=3, max_bytes=12_000_000, interval=0.25):
        self.hosts = set(hosts)
        self.timeout = timeout
        self.attempts = attempts
        self.max_bytes = max_bytes
        self.interval = interval
        self.last_request = 0.0
        self.opener = urllib.request.build_opener(Redirects(self.check_url))

    def check_url(self, url):
        p = urllib.parse.urlparse(url)
        if p.scheme != 'https' or p.hostname not in self.hosts or p.username or p.password or p.port not in (None,443):
            raise FetchError('Refused URL outside the configured HTTPS source hosts')

    def get(self, url):
        self.check_url(url)
        for attempt in range(self.attempts):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                req = urllib.request.Request(url, headers={'User-Agent':AGENT, 'Accept-Encoding':'identity'})
                with self.opener.open(req, timeout=self.timeout) as r:
                    self.check_url(r.url)
                    body = r.read(self.max_bytes + 1)
                    if len(body) > self.max_bytes:
                        raise FetchError('Response exceeded bounded download limit')
                    headers = {k.lower():v for k,v in r.headers.items()}
                    if headers.get('content-encoding') == 'gzip':
                        # Bounded decompression, including gzip-only hosts ignoring identity.
                        import io
                        with gzip.GzipFile(fileobj=io.BytesIO(body)) as z:
                            body = z.read(self.max_bytes + 1)
                        if len(body) > self.max_bytes:
                            raise FetchError('Decompressed response exceeded limit')
                    return Response(r.url, body, headers, r.status)
            except urllib.error.HTTPError as exc:
                if exc.code not in (429,500,502,503,504) or attempt+1 == self.attempts:
                    raise FetchError(f'Source returned HTTP {exc.code}', exc.code) from None
                delay = exc.headers.get('Retry-After','')
                if delay.isdigit() and int(delay)>30:
                    raise FetchError('Source requested a later retry; defer to next check', exc.code) from None
                time.sleep(min(30,int(delay)) if delay.isdigit() else 2**attempt)
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt+1 == self.attempts:
                    raise FetchError('Source connection unavailable; check host access/TLS/network') from None
                time.sleep(2**attempt)
        raise FetchError('Read failed')
