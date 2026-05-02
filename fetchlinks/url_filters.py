import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def excluded_url_host_keywords_from_sources(sources: dict) -> list[str]:
    ingest_config = sources.get('ingest', {})
    if not isinstance(ingest_config, dict):
        return []
    return normalize_host_keywords(ingest_config.get('excluded_url_host_keywords', []))


def normalize_host_keywords(keywords) -> list[str]:
    if not keywords or not isinstance(keywords, list):
        return []
    return [keyword.strip().lower() for keyword in keywords if isinstance(keyword, str) and keyword.strip()]


def url_matches_host_keyword(url: str, keywords: list[str]) -> bool:
    normalized_keywords = normalize_host_keywords(keywords)
    if not normalized_keywords:
        return False

    try:
        hostname = (urlparse(url).hostname or '').lower()
    except ValueError:
        return False

    if not hostname:
        return False

    return any(keyword in hostname for keyword in normalized_keywords)


def filter_post_urls_by_host_keywords(post, keywords: list[str]) -> int:
    kept_urls = []
    removed_count = 0
    for url in post.urls:
        if url_matches_host_keyword(url, keywords):
            removed_count += 1
            continue
        kept_urls.append(url)

    if removed_count:
        post.urls = kept_urls
        post._generate_unique_url_string()

    return removed_count


def filter_posts_by_url_host_keywords(posts: list, keywords: list[str], source_name: str) -> list:
    normalized_keywords = normalize_host_keywords(keywords)
    if not normalized_keywords:
        return posts

    filtered_posts = []
    removed_url_count = 0
    skipped_post_count = 0

    for post in posts:
        removed_url_count += filter_post_urls_by_host_keywords(post, normalized_keywords)
        if post.post_has_urls:
            filtered_posts.append(post)
        else:
            skipped_post_count += 1

    if removed_url_count:
        logger.info(
            'Filtered %s %s URL(s) matching excluded host keyword(s); skipped %s post(s) with no remaining URLs',
            removed_url_count,
            source_name,
            skipped_post_count,
        )

    return filtered_posts
