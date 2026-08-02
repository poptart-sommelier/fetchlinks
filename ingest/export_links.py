"""Export every collected URL to a plain-text file.

A Publisher-side tool: it reads the destination database, so it takes a
database URL rather than a file path and resolves it exactly the way the
Publisher does, from ``FETCHLINKS_DATABASE_URL`` or ``DATABASE_URL``.

Usage:
    python export_links.py [--out PATH] [--limit N] [--database-url URL]
"""
import argparse
import sys
from pathlib import Path

from publisher.connection import connect, resolve_database_url

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR / 'data' / 'links.txt'

# The unshortened form when one is known, otherwise the URL as collected.
# Ordered case-insensitively under the C collation rather than the server
# default, so the same database produces the same file on any host.
_QUERY = """
    SELECT COALESCE(NULLIF(u.unshortened_url, ''), u.url) AS link
    FROM content.post_urls u
    JOIN content.posts p ON p.post_id = u.post_id
    ORDER BY lower(COALESCE(NULLIF(u.unshortened_url, ''), u.url)) COLLATE "C" ASC
"""


def export_links(conn, out_path: Path, limit: int | None) -> int:
    """Write every URL to ``out_path``, one per line. Returns the count."""
    sql = _QUERY
    params = None
    if limit is not None:
        if limit < 0:
            raise ValueError('limit must not be negative')
        sql += ' LIMIT %s'
        params = (limit,)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open('w', encoding='utf-8') as fh:
        for (link,) in rows:
            fh.write(f'{link}\n')

    return len(rows)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description='Export collected URLs to a text file.')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT,
                        help='Output text file path')
    parser.add_argument('--limit', type=int, default=None,
                        help='Maximum number of URLs to export')
    parser.add_argument('--database-url', default=None,
                        help='Override the database URL')
    args = parser.parse_args(argv)

    url = resolve_database_url(args.database_url)
    with connect(url) as conn:
        count = export_links(conn, args.out, args.limit)
    print(f'Wrote {count} URLs to {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
