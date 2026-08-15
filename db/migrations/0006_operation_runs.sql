-- 0006: operational run history.
--
-- The front page can show that no new posts arrived. It cannot say whether
-- collection stopped, publishing stopped, or everything worked and there was
-- simply nothing new. This table is what makes that difference visible: one row
-- per attempt of a scheduled job, kept for a week.
--
-- It is a record of *work*, not a log. Fixed fields, counts, a normalized error
-- category and one short message. Full logs and tracebacks stay on the Pi,
-- where they can be read without paying for storage in a 0.5 GB database and
-- without turning Neon into a place secrets could be pasted by accident.
--
-- One table rather than one per job. The four jobs share far more than they
-- differ -- when did it start, did it finish, how long, did it work -- and the
-- page reads them together, ordered by time. What genuinely differs is a small
-- metric set per job, and that goes in `details`.


-- --------------------------------------------------------------------------
-- Runs
-- --------------------------------------------------------------------------
--
-- `job` is unconstrained text rather than a check constraint or enum. Adding a
-- fifth scheduled job should not require a migration, and a job name that the
-- page does not recognize is harmless: it simply appears in the recent-runs
-- table without a dedicated card.
--
-- `result` is constrained, because every reader branches on it. `running` is
-- not a transitional nicety: the publisher inserts and commits it the moment it
-- connects, so a process that is killed mid-drain leaves a visibly unfinished
-- row rather than no evidence at all. Silence and success must never look the
-- same.
--
-- `elapsed_ms` is measured by the process with a monotonic clock, not derived
-- from `finished_at - started_at`. The Pi's wall clock can step when NTP
-- corrects it, and a negative duration in the middle of an outage is exactly
-- the sort of thing that wastes an evening.

CREATE TABLE IF NOT EXISTS content.operation_runs (
    run_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job           text        NOT NULL,
    -- Natural identity for runs that arrive from elsewhere and can be replayed.
    -- A collection run is uploaded inside its batch, so its key is the batch
    -- id; publisher-side jobs write their own rows once and leave this empty.
    -- Empty rather than NULL, with a partial index below: "no natural key"
    -- is then stated in the index rather than relying on the reader knowing
    -- that NULLs do not conflict.
    run_key       text        NOT NULL DEFAULT '',
    started_at    timestamptz NOT NULL,
    finished_at   timestamptz,
    -- When the row reached the database, which for collection is up to an hour
    -- after the run itself. The page needs both: `started_at` orders history,
    -- `reported_at` is what tells you the reporting path is alive.
    reported_at   timestamptz NOT NULL DEFAULT now(),
    result        text        NOT NULL,
    elapsed_ms    bigint,
    -- A small stable vocabulary (network, timeout, authentication, rate_limit,
    -- http, invalid_response, parse, unknown), not an exception class name.
    error_kind    text        NOT NULL DEFAULT '',
    error_message text        NOT NULL DEFAULT '',
    -- The job-specific metric set. The database enforces only that it is an
    -- object; the shape is owned by the Python record types and validated
    -- again by the web reader, because a JSON column that everything casts
    -- blindly is a schema nobody maintains.
    details       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT operation_runs_job_present CHECK (job <> ''),
    CONSTRAINT operation_runs_result_known CHECK (
        result IN ('running', 'ok', 'partial', 'failed')
    ),
    -- A finished run has a finish time and an unfinished one does not. Without
    -- this, a bug that forgets to set `finished_at` reads as a run that is
    -- still going, forever.
    CONSTRAINT operation_runs_finished_matches_result CHECK (
        (result = 'running') = (finished_at IS NULL)
    ),
    CONSTRAINT operation_runs_elapsed_non_negative CHECK (
        elapsed_ms IS NULL OR elapsed_ms >= 0
    ),
    -- Bounded here as well as in Python. This is the one field carrying text
    -- from a stranger's website, and an unbounded error string is how a
    -- multi-megabyte HTML page ends up in a row that was meant to be a note.
    CONSTRAINT operation_runs_error_message_bounded CHECK (
        char_length(error_message) <= 500
    ),
    CONSTRAINT operation_runs_error_kind_bounded CHECK (
        char_length(error_kind) <= 40
    ),
    CONSTRAINT operation_runs_details_is_object CHECK (
        jsonb_typeof(details) = 'object'
    )
);

-- Replay safety for runs that ride in a batch. The publisher applies a batch in
-- one transaction, so a conflict here aborts the whole thing exactly as the
-- batch ledger does -- a re-published batch cannot record its collection twice.
-- Partial, so the many publisher-side rows with no natural key do not collide.
CREATE UNIQUE INDEX IF NOT EXISTS idx_operation_runs_key
    ON content.operation_runs (job, run_key)
    WHERE run_key <> '';

-- Every question the status page asks is "the latest runs of this job", and
-- retention deletes by age across all jobs.
CREATE INDEX IF NOT EXISTS idx_operation_runs_job_started
    ON content.operation_runs (job, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_operation_runs_started
    ON content.operation_runs (started_at DESC);


-- --------------------------------------------------------------------------
-- Grants
-- --------------------------------------------------------------------------
--
-- None, deliberately, for the reason given in 0004: 0003 already set default
-- privileges on this schema, so the web role can read this table and the
-- publisher can write it. Restating them here would also make this migration
-- depend on those roles existing, which the web application's test harness
-- does not arrange.
