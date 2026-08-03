"""Where the collector and publisher keep their files.

The runtime directory lives outside the checkout so that deploying new code
never disturbs queued batches or resume state, and so a rollback cannot revert
a cursor. One place resolves these paths so the collector and publisher cannot
disagree about which spool they are talking about.
"""

import os
from pathlib import Path

from .spool import Spool
from .state import STATE_FILENAME, CollectorState

CATALOG_FILENAME = 'catalog.v1.json'

RUNTIME_DIR_ENV = 'FETCHLINKS_RUNTIME_DIR'
DEFAULT_RUNTIME_DIR = '~/.fetchlinks/runtime'


class RuntimeLayout:
    """Resolves the runtime directory and the things stored under it.

    ::

        runtime/
          catalog/catalog.v1.json     what to collect, exported by a publisher
          state/collector-state.v1.json   where the collector got to
          outbox/                     the batch spool
    """

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def __repr__(self) -> str:
        return f'<RuntimeLayout {self.root}>'

    @classmethod
    def resolve(cls, root=None) -> 'RuntimeLayout':
        """Pick the runtime directory: explicit argument, environment, default."""
        if root:
            return cls(root)
        from_env = os.environ.get(RUNTIME_DIR_ENV)
        if from_env:
            return cls(from_env)
        return cls(DEFAULT_RUNTIME_DIR)

    @property
    def catalog_dir(self) -> Path:
        return self.root / 'catalog'

    @property
    def catalog_path(self) -> Path:
        return self.catalog_dir / CATALOG_FILENAME

    @property
    def state_dir(self) -> Path:
        return self.root / 'state'

    @property
    def state_path(self) -> Path:
        return self.state_dir / STATE_FILENAME

    @property
    def outbox_dir(self) -> Path:
        return self.root / 'outbox'

    def initialize(self) -> 'RuntimeLayout':
        self.catalog_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.spool().initialize()
        return self

    def spool(self) -> Spool:
        return Spool(self.outbox_dir)

    def load_state(self) -> CollectorState:
        return CollectorState.load(self.state_path)

    def save_state(self, state: CollectorState) -> None:
        state.save(self.state_path)
