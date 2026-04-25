"""Safely resolve shortened URLs to their final destinations.

Hardened against SSRF: rejects redirects to non-public IP addresses and
non-http(s) schemes. Manually follows redirects so each hop is checked.

Public API:
    is_shortened(url)  -> bool
    unshorten_url(url) -> Optional[str]
    unshorten_urls(iterable[str], max_workers=10) -> dict[str, str]
"""
import ipaddress
import logging
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, Optional
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

# Known URL shortener hostnames. Domain-suffix match (so 'foo.bit.ly' matches).
SHORTENER_DOMAINS = frozenset({
    'bit.ly',
    'buff.ly',
    'cutt.ly',
    'dlvr.it',
    'fb.me',
    'goo.gl',
    'is.gd',
    'lnkd.in',
    'ow.ly',
    'rb.gy',
    'rebrand.ly',
    'sans.org',  # sans.org/u/<id>
    'shorturl.at',
    't.co',
    'tinyurl.com',
    'tiny.cc',
    'trib.al',
    'wp.me',
    'youtu.be',
})

MAX_REDIRECTS = 5
REQUEST_TIMEOUT_SECONDS = 5
DEFAULT_THREADS = 10
USER_AGENT = 'fetchlinks-unshortener/0.1'


def is_shortened(url: str) -> bool:
    """Return True if URL hostname matches a known shortener domain."""
    try:
        host = (urlparse(url).hostname or '').lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host == d or host.endswith('.' + d) for d in SHORTENER_DOMAINS)


def _is_safe_target(url: str) -> bool:
    """Reject non-http(s) schemes and any URL whose host resolves to a
    non-public IP (private, loopback, link-local, multicast, reserved,
    unspecified). Resolves DNS to catch hostnames that point at internal IPs.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https'):
        return False
    host = parsed.hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
    return True


def _request_no_follow(session: requests.Session, url: str) -> Optional[requests.Response]:
    """Issue HEAD with no auto-redirect. Falls back to GET on 405/501."""
    try:
        resp = session.head(url, allow_redirects=False, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.debug('HEAD failed for %s: %s', url, exc)
        return None
    if resp.status_code in (405, 501):
        try:
            # stream=True so we don't download the body for non-redirects either.
            resp = session.get(url, allow_redirects=False, timeout=REQUEST_TIMEOUT_SECONDS, stream=True)
            resp.close()
        except requests.RequestException as exc:
            logger.debug('GET fallback failed for %s: %s', url, exc)
            return None
    return resp


def unshorten_url(url: str, session: Optional[requests.Session] = None) -> Optional[str]:
    """Manually follow up to MAX_REDIRECTS hops, SSRF-checking each one.

    Returns the final URL on success, or None on failure / unsafe target.
    """
    sess = session if session is not None else requests.Session()
    if session is None:
        sess.headers['User-Agent'] = USER_AGENT

    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not _is_safe_target(current):
            logger.warning('Rejecting unsafe URL during unshorten: %s', current)
            return None

        resp = _request_no_follow(sess, current)
        if resp is None:
            return None

        if 300 <= resp.status_code < 400:
            location = resp.headers.get('Location')
            if not location:
                return current
            current = urljoin(current, location)
            continue

        # Non-redirect response — we've arrived.
        return current

    logger.warning('Exceeded redirect limit for %s', url)
    return None


def unshorten_urls(urls: Iterable[str], max_workers: int = DEFAULT_THREADS) -> Dict[str, str]:
    """Concurrently resolve shortened URLs.

    Returns {original_url: final_url} only for URLs that were detected as
    shortened, successfully resolved, and whose final URL differs from the
    original. URLs that aren't shortened or fail resolution are omitted.
    """
    targets = list({u for u in urls if is_shortened(u)})
    if not targets:
        return {}

    results: Dict[str, str] = {}
    with requests.Session() as session:
        session.headers['User-Agent'] = USER_AGENT
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_url = {pool.submit(unshorten_url, u, session): u for u in targets}
            for future in as_completed(future_to_url):
                original = future_to_url[future]
                try:
                    final = future.result()
                except Exception as exc:  # defensive; unshorten_url already swallows
                    logger.debug('unshorten_url raised for %s: %s', original, exc)
                    continue
                if final and final != original:
                    results[original] = final
    return results
