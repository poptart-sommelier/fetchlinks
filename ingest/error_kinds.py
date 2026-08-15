"""Turn one failure into one of a handful of stable categories.

The categories exist so that "eighteen feeds timed out" can be compared with
last week, which an exception class name cannot be: it changes when a library
is upgraded, and it is unreadable to anyone who is not holding the source.

Deliberately coarse. The purpose is to point at the right place to look --
the network, the far end, our credentials, the shape of the reply -- not to
diagnose from the category alone. The exact text stays in the collector's own
rotating log, which is where a real diagnosis happens.
"""

import socket

import requests

from pipeline.contract import (
    ERROR_KIND_NONE,
    ERROR_KIND_UNKNOWN,
    clean_error_message,
)

NETWORK = 'network'
TIMEOUT = 'timeout'
AUTHENTICATION = 'authentication'
RATE_LIMIT = 'rate_limit'
HTTP = 'http'
INVALID_RESPONSE = 'invalid_response'
PARSE = 'parse'
UNKNOWN = ERROR_KIND_UNKNOWN
NONE = ERROR_KIND_NONE


def from_status(status: int | None) -> str:
    """Categorize an HTTP status, or its absence.

    Zero or None means the request never got a reply at all, which is a
    network fault rather than a status of zero.
    """
    if not status:
        return NETWORK
    if status in (401, 403):
        return AUTHENTICATION
    if status == 429:
        return RATE_LIMIT
    if status == 408:
        return TIMEOUT
    if 200 <= status < 400:
        return NONE
    return HTTP


def from_exception(exc: BaseException) -> str:
    if isinstance(exc, requests.exceptions.Timeout):
        return TIMEOUT
    if isinstance(exc, requests.exceptions.HTTPError):
        response = getattr(exc, 'response', None)
        return from_status(getattr(response, 'status_code', None)) or HTTP
    if isinstance(exc, requests.exceptions.ConnectionError):
        return NETWORK
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return TIMEOUT
    if isinstance(exc, (socket.gaierror, ConnectionError, OSError)):
        return NETWORK
    if isinstance(exc, requests.exceptions.RequestException):
        return NETWORK
    if isinstance(exc, ValueError):
        # Which covers JSONDecodeError, and is how every source in this
        # codebase discovers that a reply was not what it claimed to be.
        return INVALID_RESPONSE
    return UNKNOWN


def describe(exc: BaseException) -> str:
    """A short label for an exception, without a traceback.

    The class name is included because some exceptions -- notably the
    connection ones -- carry an empty message, and "network failure" with no
    further detail is less useful than "ConnectionError".
    """
    text = str(exc).strip()
    name = type(exc).__name__
    return clean_error_message(f'{name}: {text}' if text else name)


def from_rss_error(status: int | None, error: str | None) -> str:
    """Categorize one RSS fetch outcome as the feed reader reports it.

    RSS is instrumented from its observations rather than from exceptions,
    because those observations are already the source's honest per-feed
    account of what happened and are what the publisher stores.
    """
    if not error:
        return NONE
    text = (error or '').lower()
    if 'timeout' in text:
        return TIMEOUT
    if 'parse error' in text:
        return PARSE
    if status:
        return from_status(status)
    return NETWORK
