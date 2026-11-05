"""Serve cached public product images without changing their catalog source URLs."""
import hashlib
import os
from pathlib import Path


def photo_root():
    return Path(os.environ.get('KROY_PHOTO_DIR', 'var/product-photos'))


def photo_key(url):
    return hashlib.sha256(url.encode()).hexdigest()


def photo_preview(url):
    if url and (cacheable(url) or (photo_root() / (photo_key(url) + '.img')).is_file()):
        return 'api/photos/' + photo_key(url)
    return url


def image_type(data):
    if data.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return 'image/webp'
    return None


def cacheable(url):
    from urllib.parse import urlparse
    import re
    try:
        p = urlparse(url)
        port = p.port
    except ValueError:
        return False
    return p.scheme == 'https' and not p.username and not p.password and port in (None, 443) and bool(re.fullmatch(r'basket-\d+\.wbbasket\.ru|[a-z0-9-]+\.wbstatic\.net', p.hostname or ''))


def cache_photo(url):
    """Fetch only a known catalog image from approved public image hosts."""
    import tempfile
    import urllib.request
    if not cacheable(url): return None
    root = photo_root();path = root / (photo_key(url) + '.img')
    if path.is_file(): return path
    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not cacheable(newurl): raise ValueError('Unsupported image redirect')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    opener = urllib.request.build_opener(SafeRedirect())
    with opener.open(urllib.request.Request(url, headers={'Accept':'image/webp,image/*'}), timeout=8) as response:
        data = response.read(8_000_001)
    if len(data) > 8_000_000 or not image_type(data[:16]): raise ValueError('Invalid product image')
    root.mkdir(parents=True, exist_ok=True)
    # Only complete validated images become visible to concurrent requests.
    with tempfile.NamedTemporaryFile(dir=root, delete=False) as f:
        f.write(data);tmp = Path(f.name)
    tmp.chmod(0o644);tmp.replace(path)
    return path
