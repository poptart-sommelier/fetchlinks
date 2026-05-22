"""Typed configuration loader for Fetchlinks ingest.

Loads ``fetchlinks.toml`` into a frozen dataclass tree. Path values are
resolved relative to the TOML file itself (so paths in the dev config
work regardless of cwd), unless the operator supplies absolute paths
(as the production /etc/fetchlinks/fetchlinks.toml does).
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import ingest_limits

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / 'data' / 'config' / 'fetchlinks.toml'

_VALID_LOG_LEVELS = {'CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'}


# --- Dataclasses -----------------------------------------------------------

@dataclass(frozen=True)
class PathsConfig:
    db: Path
    log_file: Path
    log_level: str = 'INFO'


@dataclass(frozen=True)
class IngestPolicy:
    max_post_age_months: int = ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS
    excluded_url_host_keywords: tuple[str, ...] = ()
    excluded_url_or_description_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class RssSource:
    enabled: bool
    feeds_file: Path
    feeds: tuple[str, ...]


@dataclass(frozen=True)
class RedditSource:
    enabled: bool
    credential_location: Path
    subreddits: tuple[str, ...]
    listing_limit: int = 100
    max_pages: int = 5


@dataclass(frozen=True)
class BlueskySource:
    enabled: bool
    credential_location: Path
    timeline_limit: int = 50


@dataclass(frozen=True)
class MastodonInstance:
    name: str
    instance_url: str
    credential_location: Path
    timeline: str = 'home'
    timeline_limit: int = 40
    enabled: bool = True


@dataclass(frozen=True)
class MastodonSource:
    enabled: bool
    instances: tuple[MastodonInstance, ...]


@dataclass(frozen=True)
class Sources:
    rss: RssSource | None = None
    reddit: RedditSource | None = None
    bluesky: BlueskySource | None = None
    mastodon: MastodonSource | None = None


@dataclass(frozen=True)
class AppConfig:
    paths: PathsConfig
    ingest: IngestPolicy
    sources: Sources
    source_path: Path = field(default_factory=Path)


# --- Loader ----------------------------------------------------------------

def load_config(config_path: Path) -> AppConfig:
    """Load and validate ``fetchlinks.toml`` at ``config_path``."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f'Config file does not exist: {config_path}')

    with open(config_path, 'rb') as fh:
        raw = tomllib.load(fh)

    base = config_path.resolve().parent
    paths = _build_paths(raw.get('paths', {}), base)
    ingest = _build_ingest(raw.get('ingest', {}))
    sources = _build_sources(raw.get('sources', {}), base)

    # Ensure log directory exists; mirrors old behaviour.
    paths.log_file.parent.mkdir(parents=True, exist_ok=True)

    return AppConfig(paths=paths, ingest=ingest, sources=sources, source_path=config_path)


# --- Builders --------------------------------------------------------------

def _resolve_path(value: Any, base: Path, *, expanduser: bool = False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Path value must be a non-empty string')
    p = Path(value).expanduser() if expanduser else Path(value)
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def _build_paths(section: dict, base: Path) -> PathsConfig:
    if not isinstance(section, dict):
        raise ValueError('[paths] must be a table')
    for required in ('db', 'log_file'):
        if required not in section:
            raise ValueError(f'[paths] missing required field: {required}')

    log_level = str(section.get('log_level', 'INFO')).upper()
    if log_level not in _VALID_LOG_LEVELS:
        raise ValueError(f'[paths] log_level must be one of {sorted(_VALID_LOG_LEVELS)}')

    return PathsConfig(
        db=_resolve_path(section['db'], base),
        log_file=_resolve_path(section['log_file'], base),
        log_level=log_level,
    )


def _build_ingest(section: dict) -> IngestPolicy:
    if not isinstance(section, dict):
        raise ValueError('[ingest] must be a table')

    max_age = section.get('max_post_age_months', ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS)
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age < 1:
        raise ValueError('[ingest] max_post_age_months must be a positive integer')

    host_kw = section.get('excluded_url_host_keywords', [])
    desc_kw = section.get('excluded_url_or_description_keywords', [])
    _validate_keyword_list('excluded_url_host_keywords', host_kw)
    _validate_keyword_list('excluded_url_or_description_keywords', desc_kw)

    return IngestPolicy(
        max_post_age_months=max_age,
        excluded_url_host_keywords=tuple(host_kw),
        excluded_url_or_description_keywords=tuple(desc_kw),
    )


def _validate_keyword_list(name: str, value: Any) -> None:
    if not isinstance(value, list):
        raise ValueError(f'[ingest] {name} must be a list of strings')
    for kw in value:
        if not isinstance(kw, str) or not kw.strip():
            raise ValueError(f'[ingest] {name} must contain non-empty strings')


def _build_sources(section: dict, base: Path) -> Sources:
    if not isinstance(section, dict):
        raise ValueError('[sources] must be a table')

    return Sources(
        rss=_build_rss(section.get('rss'), base),
        reddit=_build_reddit(section.get('reddit'), base),
        bluesky=_build_bluesky(section.get('bluesky'), base),
        mastodon=_build_mastodon(section.get('mastodon'), base),
    )


def _build_rss(section: dict | None, base: Path) -> RssSource | None:
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ValueError('[sources.rss] must be a table')

    enabled = bool(section.get('enabled', True))
    feeds_file_value = section.get('feeds_file')
    if not feeds_file_value:
        raise ValueError('[sources.rss] feeds_file is required')
    feeds_file = _resolve_path(feeds_file_value, base)

    feeds: tuple[str, ...] = ()
    if enabled:
        if not feeds_file.exists():
            raise FileNotFoundError(f'RSS feeds file not found: {feeds_file}')
        feeds = tuple(_read_feeds_file(feeds_file))
        if not feeds:
            raise ValueError(f'RSS feeds file contains no feeds: {feeds_file}')

    return RssSource(enabled=enabled, feeds_file=feeds_file, feeds=feeds)


def _read_feeds_file(path: Path) -> list[str]:
    """Read one URL per line; skip blank lines and ``#`` comments."""
    out: list[str] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        out.append(stripped)
    return out


def _build_reddit(section: dict | None, base: Path) -> RedditSource | None:
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ValueError('[sources.reddit] must be a table')

    enabled = bool(section.get('enabled', True))
    cred = section.get('credential_location')
    if enabled and not cred:
        raise ValueError('[sources.reddit] credential_location required when enabled')
    cred_path = _expand_credential(cred, base, label='reddit') if enabled else _maybe_path(cred, base)

    subreddits = section.get('subreddits')
    if enabled:
        if not isinstance(subreddits, list) or not subreddits:
            raise ValueError('[sources.reddit] subreddits must be a non-empty list')
        for sr in subreddits:
            if not isinstance(sr, str) or not sr.strip():
                raise ValueError('[sources.reddit] subreddits must be non-empty strings')

    listing_limit = section.get('listing_limit', 100)
    if not isinstance(listing_limit, int) or isinstance(listing_limit, bool) or listing_limit < 1:
        raise ValueError('[sources.reddit] listing_limit must be a positive integer')

    max_pages = section.get('max_pages', 5)
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages < 1:
        raise ValueError('[sources.reddit] max_pages must be a positive integer')

    return RedditSource(
        enabled=enabled,
        credential_location=cred_path or Path(),
        subreddits=tuple(subreddits or ()),
        listing_limit=listing_limit,
        max_pages=max_pages,
    )


def _build_bluesky(section: dict | None, base: Path) -> BlueskySource | None:
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ValueError('[sources.bluesky] must be a table')

    enabled = bool(section.get('enabled', False))
    cred = section.get('credential_location')
    if enabled and not cred:
        raise ValueError('[sources.bluesky] credential_location required when enabled')
    cred_path = _expand_credential(cred, base, label='bluesky') if enabled else _maybe_path(cred, base)

    timeline_limit = section.get('timeline_limit', 50)
    if not isinstance(timeline_limit, int) or isinstance(timeline_limit, bool) or timeline_limit < 1:
        raise ValueError('[sources.bluesky] timeline_limit must be a positive integer')

    return BlueskySource(
        enabled=enabled,
        credential_location=cred_path or Path(),
        timeline_limit=timeline_limit,
    )


def _build_mastodon(section: dict | None, base: Path) -> MastodonSource | None:
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ValueError('[sources.mastodon] must be a table')

    enabled = bool(section.get('enabled', False))
    instances_raw = section.get('instances', [])
    if not isinstance(instances_raw, list):
        raise ValueError('[sources.mastodon.instances] must be a list of tables')

    if enabled and not instances_raw:
        raise ValueError('[sources.mastodon] requires at least one instance when enabled')

    instances: list[MastodonInstance] = []
    seen: set[str] = set()
    for raw in instances_raw:
        if not isinstance(raw, dict):
            raise ValueError('[[sources.mastodon.instances]] entries must be tables')

        instance_enabled = bool(raw.get('enabled', True))
        name = raw.get('name')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Mastodon instance requires a non-empty name')
        if name in seen:
            raise ValueError(f'Duplicate Mastodon instance name: {name}')
        seen.add(name)

        instance_url = raw.get('instance_url')
        parsed = urlparse(instance_url) if isinstance(instance_url, str) else None
        if parsed is None or parsed.scheme != 'https' or not parsed.netloc:
            raise ValueError(f'Mastodon instance {name!r} requires https instance_url')

        cred = raw.get('credential_location')
        if instance_enabled and not cred:
            raise ValueError(f'Mastodon instance {name!r} requires credential_location when enabled')
        cred_path = (
            _expand_credential(cred, base, label=f'mastodon instance {name}')
            if instance_enabled
            else _maybe_path(cred, base)
        )

        timeline = raw.get('timeline', 'home')
        if timeline != 'home':
            raise ValueError(f'Mastodon instance {name!r} timeline must be "home"')

        timeline_limit = raw.get('timeline_limit', 40)
        if not isinstance(timeline_limit, int) or isinstance(timeline_limit, bool) or timeline_limit < 1:
            raise ValueError(f'Mastodon instance {name!r} timeline_limit must be a positive integer')

        instances.append(MastodonInstance(
            name=name,
            instance_url=instance_url,
            credential_location=cred_path or Path(),
            timeline=timeline,
            timeline_limit=timeline_limit,
            enabled=instance_enabled,
        ))

    return MastodonSource(enabled=enabled, instances=tuple(instances))


def _maybe_path(value: Any, base: Path) -> Path | None:
    if not value:
        return None
    return _resolve_path(value, base, expanduser=True)


def _expand_credential(value: Any, base: Path, *, label: str) -> Path:
    path = _resolve_path(value, base, expanduser=True)
    if not path.exists():
        raise FileNotFoundError(f'{label} credential file not found at {path}')
    return path


# --- CLI -------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Fetchlinks ingest')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG,
                        help='Path to fetchlinks.toml (default: %(default)s)')
    return parser.parse_args(sys.argv[1:])
