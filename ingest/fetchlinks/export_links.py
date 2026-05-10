"""Export URLs from the fetchlinks database to a plain-text file.

Usage:
    python export_links.py [--db PATH] [--out PATH] [--limit N]

Defaults:
    --db   data/config/... resolved from config.json (same as fetch_links.py)
    --out  /tmp/links.txt
    --limit  no limit
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).parent / 'data' / 'config' / 'config.json'
DEFAULT_OUT = Path(__file__).parent / 'data' / 'links.txt'


def resolve_db_path(config_path: Path) -> Path:
    with config_path.open() as f:
        cfg = json.load(f)
    db_info = cfg['db_info']
    db_path = Path(db_info['db_location']) / db_info['db_name']
    if not db_path.is_absolute():
        # Resolve relative to the script directory (same convention as fetch_links.py)
        db_path = Path(__file__).parent / db_path
    return db_path


def export_links(db_path: Path, out_path: Path, limit: int | None) -> int:
    if not db_path.exists():
        raise FileNotFoundError(f'Database not found: {db_path}')

    sql = """
        SELECT COALESCE(NULLIF(u.unshortened_url, ''), u.url) AS link
        FROM post_urls u
        JOIN posts p ON p.idx = u.post_id
        ORDER BY link COLLATE NOCASE ASC
    """
    if limit is not None:
        sql += f' LIMIT {int(limit)}'

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(sql).fetchall()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open('w', encoding='utf-8') as f:
        for (link,) in rows:
            f.write(f'{link}\n')

    return len(rows)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description='Export URLs from fetchlinks DB to a text file.')
    parser.add_argument('--db', type=Path, help='Path to SQLite DB. Default: read from config.json')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG, help='Path to config.json')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT, help='Output text file path')
    parser.add_argument('--limit', type=int, default=None, help='Maximum number of URLs to export')
    args = parser.parse_args(argv)

    db_path = args.db if args.db is not None else resolve_db_path(args.config)
    count = export_links(db_path, args.out, args.limit)
    print(f'Wrote {count} URLs to {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
