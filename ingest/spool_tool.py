"""Inspect and exercise the batch spool from the command line.

The spool is the handover point between collection and publication, so when
something looks wrong the first question is always "what is actually queued?".
This answers that without a database, a publisher, or a running collector.

    python spool_tool.py status
    python spool_tool.py list ready
    python spool_tool.py show <batch-id>
    python spool_tool.py verify <batch-id>
    python spool_tool.py demo

``--runtime`` overrides the runtime directory, otherwise FETCHLINKS_RUNTIME_DIR
and then the default location are used.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from pipeline import (
    BlueskyFollowRecord,
    CheckpointRecord,
    ContractError,
    MastodonFollowRecord,
    PostRecord,
    RssObservationRecord,
    RuntimeLayout,
)
from pipeline import contract, spool as spool_module

PREVIEW_LINES = 3


def _find_batch(spool, batch_id):
    contract.validate_batch_id(batch_id)
    for stage in spool_module.STAGES:
        path = spool.batch_path(stage, batch_id)
        if path.is_dir():
            return stage, path
    raise SystemExit(f'No batch {batch_id} anywhere in {spool.root}')


def cmd_status(layout, _args):
    spool = layout.spool()
    spool.initialize()
    stats = spool.queue_stats()
    print(f'runtime:  {layout.root}')
    print(f'catalog:  {"present" if layout.catalog_path.is_file() else "MISSING"}')
    print(f'state:    {"present" if layout.state_path.is_file() else "not yet written"}')
    for stage, count in stats['counts'].items():
        print(f'  {stage:<11} {count}')
    age = stats['oldest_outstanding_age_seconds']
    if age is None:
        print('  queue is empty')
    else:
        print(f'  oldest waiting batch {stats["oldest_outstanding_batch_id"]} '
              f'({age // 60} minutes old)')
    print(f'  disk usage  {stats["disk_bytes"]:,} bytes')
    return 0


def cmd_list(layout, args):
    spool = layout.spool()
    spool.initialize()
    for batch_id in spool.batch_ids(args.stage):
        print(f'{batch_id}  {spool_module.batch_id_created_at(batch_id).isoformat()}')
    return 0


def cmd_show(layout, args):
    stage, path = _find_batch(layout.spool(), args.batch_id)
    print(f'stage: {stage}')
    print((path / contract.MANIFEST_FILENAME).read_text('utf-8'))
    for entry in sorted(p for p in path.iterdir() if p.suffix == '.ndjson'):
        print(f'--- {entry.name}')
        with entry.open('r', encoding='utf-8', newline='') as handle:
            for number, line in enumerate(handle, start=1):
                if number > PREVIEW_LINES:
                    print('    ...')
                    break
                print(f'    {line.rstrip()}')
    failure = path / spool_module.FAILURE_FILENAME
    if failure.is_file():
        print(f'--- {spool_module.FAILURE_FILENAME}')
        print(failure.read_text('utf-8'))
    return 0


def cmd_verify(layout, args):
    spool = layout.spool()
    stage, _path = _find_batch(spool, args.batch_id)
    # Verification reads a batch in place, so point a ClaimedBatch at whatever
    # stage it is in rather than moving it and disturbing the queue.
    batch = spool_module.ClaimedBatch(spool, args.batch_id)
    batch.path = spool.batch_path(stage, args.batch_id)
    try:
        manifest = batch.verify()
    except ContractError as exc:
        print(f'INVALID: {exc}')
        return 1
    print(f'valid: {manifest.total_records} records across {len(manifest.files)} files')
    for entry in manifest.files:
        scope = f' [{entry.scope}]' if entry.scope else ''
        print(f'  {entry.name}{scope}: {entry.record_count} records')
    return 0


def cmd_demo(layout, _args):
    """Write, claim, retry, and archive a synthetic batch."""
    spool = layout.spool()
    checkpoints = [
        CheckpointRecord('reddit', 'netsec', 't3_abc', '2026-01-02T04:00:00Z'),
        CheckpointRecord('bluesky', 'timeline', 'cursor-1', '2026-01-02T04:00:00Z'),
    ]
    with spool.new_batch(collector_version='demo', catalog_revision='rev-1') as writer:
        writer.add_posts([
            PostRecord(
                unique_id='uid-1', source='https://example.com', source_type='rss',
                posted_at='2026-01-02 03:04:05',
                urls=('https://example.com/article',),
                author='Example', description='An article with a link',
            ),
            PostRecord(
                unique_id='uid-2', source='https://www.reddit.com/r/netsec',
                source_type='reddit', posted_at='2026-01-02T04:00:00Z',
                urls=('https://example.org/advisory',),
                author='someone', description='An advisory',
                direct_link='https://www.reddit.com/r/netsec/comments/abc',
            ),
        ])
        writer.add_rss_observations([
            RssObservationRecord(
                normalized_url='https://example.com/feed', feed_url='https://example.com/feed',
                observed_at='2026-01-02T03:04:05Z', success=True, status=200,
                etag='W/"abc"', site_link='https://example.com',
            ),
            RssObservationRecord(
                normalized_url='https://broken.example/feed',
                feed_url='https://broken.example/feed',
                observed_at='2026-01-02T03:04:05Z', success=False, status=503,
                error='service unavailable',
            ),
        ])
        writer.add_checkpoints(checkpoints)
        writer.set_bluesky_follows(
            [BlueskyFollowRecord('did:plc:abc', 'someone.bsky.social', 'Someone')],
            observed_at='2026-01-02T04:00:00Z',
        )
        writer.set_mastodon_follows(
            'infosec',
            [MastodonFollowRecord('42', 'someone@infosec.exchange', 'Someone')],
            observed_at='2026-01-02T04:00:00Z',
        )

    # A collector advances its own state only once the batch is durably queued.
    state = layout.load_state()
    state.apply_checkpoints(checkpoints)
    state.set_rss_headers('https://example.com/feed', 'W/"abc"', None)
    layout.save_state(state)
    print(f'queued      {writer.batch_id}')

    ready = spool.batch_path('ready', writer.batch_id)
    print('files       ' + ', '.join(sorted(p.name for p in ready.iterdir())))
    print('\nmanifest.json')
    print(json.dumps(json.loads((ready / contract.MANIFEST_FILENAME).read_text('utf-8')), indent=2))

    first = spool.claim_next()
    first.verify()
    print(f'\nclaimed     {first.batch_id}, then the publisher crashes')

    retried = spool.claim_next()
    manifest = retried.verify()
    print(f'recovered   {retried.batch_id}')
    print(f'validated   {manifest.total_records} records, no database involved')
    print('posts       ' + ', '.join(
        record['unique_id'] for record in retried.records(contract.KIND_POSTS)
    ))
    retried.mark_published()
    print(f'archived    {spool.batch_ids("published")}')

    state = layout.load_state()
    print(f"\ncursors     reddit/netsec={state.checkpoint('reddit', 'netsec')}, "
          f"bluesky/timeline={state.checkpoint('bluesky', 'timeline')}")
    print(f"rss cache   {state.rss_headers('https://example.com/feed')}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--runtime', help='Runtime directory to operate on')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('status', help='Summarize the queue').set_defaults(func=cmd_status)

    listing = sub.add_parser('list', help='List batch ids in a stage')
    listing.add_argument('stage', choices=spool_module.STAGES)
    listing.set_defaults(func=cmd_list)

    show = sub.add_parser('show', help='Print a batch manifest and a record preview')
    show.add_argument('batch_id')
    show.set_defaults(func=cmd_show)

    verify = sub.add_parser('verify', help='Validate a batch in place')
    verify.add_argument('batch_id')
    verify.set_defaults(func=cmd_verify)

    demo = sub.add_parser(
        'demo', help='Run a synthetic batch through the whole lifecycle'
    )
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.runtime:
        layout = RuntimeLayout(args.runtime).initialize()
        return args.func(layout, args)
    if args.command == 'demo':
        # Keep the demo out of the real runtime directory unless asked.
        with tempfile.TemporaryDirectory() as temporary:
            layout = RuntimeLayout(Path(temporary) / 'runtime').initialize()
            print(f'runtime     {layout.root} (temporary)\n')
            return args.func(layout, args)
    return args.func(RuntimeLayout.resolve().initialize(), args)


if __name__ == '__main__':
    sys.exit(main())
