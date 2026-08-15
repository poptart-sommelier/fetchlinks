/**
 * The operating guide, as a page of its own.
 *
 * It lives on the site rather than in a repository document for one reason: the
 * moment it is needed is the moment something is broken, and a document in a
 * checkout on a laptop is not where anyone looks at that point. It is entirely
 * static and never touches the database, so it still answers when the status
 * page above it cannot -- which is precisely when the diagnosis steps matter.
 */

import Link from "next/link";

const RUNTIME = "~/fetchlinks/runtime";
const PY = "~/fetchlinks/.venv/bin/python";

export const metadata = {
  title: "How to read System status",
};

export default function StatusGuidePage() {
  return (
    <main className="shell">
      <header className="page-header">
        <div className="page-title">
          <p className="eyebrow">
            <Link href="/flightdeck/status">&larr; System status</Link>
          </p>
          <h1>How to read System status</h1>
        </div>
      </header>
      <StatusGuide />
    </main>
  );
}

export function StatusGuide() {
  return (
    <section aria-labelledby="guide-heading" className="status-guide">
      <h2 id="guide-heading">Operating guide</h2>
      <p className="status-guide-intro">
        Everything on the status page comes from one table that the Raspberry Pi
        writes, one row per job run, kept for seven days. Nothing else reports.
        If the Pi cannot reach the database it cannot write &ldquo;I cannot
        reach the database&rdquo; into it &mdash; which is why a stale heartbeat
        is described there as &ldquo;has not reported&rdquo; rather than as the
        Pi being off.
      </p>

      <h3>What normal looks like</h3>
      <ul className="status-guide-list">
        <li>
          <strong>Collector</strong>, every 30 minutes. It never touches the
          database; it writes a batch of posts into a folder on the Pi.
        </li>
        <li>
          <strong>Publisher</strong>, hourly at seven minutes past. It sends the
          waiting batches to the database and then refreshes the feed list. This
          is the only job that holds the database password.
        </li>
        <li>
          <strong>Retention</strong>, Sunday morning. Deletes posts past the keep
          window and trims this page&rsquo;s own history to seven days.
        </li>
        <li>
          Collection reports travel inside the batches, so a collection run only
          becomes visible here when the next publish carries it over. Up to an
          hour of lag is normal and is not a fault. That is why the Collector is
          measured against the Publisher&rsquo;s last report rather than against
          the clock.
        </li>
      </ul>

      <h3>What the health labels mean</h3>
      <p>
        These describe whether a job is <em>reporting</em>, which is a separate
        question from whether its last run went well.
      </p>
      <ul className="status-guide-list">
        <li>
          <strong>Healthy</strong> &mdash; reported within one expected gap.
        </li>
        <li>
          <strong>Delayed</strong> &mdash; one run missed. Usually a reboot, a
          slow network, or a run that took longer than usual. Worth a glance, not
          an evening.
        </li>
        <li>
          <strong>Stopped</strong> &mdash; two or more runs missed. Something
          needs doing.
        </li>
        <li>
          <strong>Unknown</strong> &mdash; only ever shown for the Collector, and
          only while the Publisher is stale. Collection facts arrive through the
          Publisher, so with that path down the Collector&rsquo;s state cannot
          honestly be known from here.
        </li>
        <li>
          <strong>Quiet</strong> &mdash; everything ran and nothing new arrived.
          Not a fault: a healthy run adds nothing when every link it found was
          already stored.
        </li>
        <li>
          <strong>Missing</strong> in the 24-hour strip &mdash; no run started in
          a window where one was expected. This is what a stopped timer looks
          like.
        </li>
      </ul>

      <h3>What a run&rsquo;s outcome means</h3>
      <p>
        Separate from the words above, every run records how it went. These are
        the words in the Recent runs table and on each source.
      </p>
      <ul className="status-guide-list">
        <li>
          <strong>ok</strong> &mdash; every part of the run succeeded.
        </li>
        <li>
          <strong>partial</strong> &mdash; some parts succeeded and some did not.
          The usual cause is a handful of the several hundred RSS feeds timing
          out or returning an error while the rest were fine. Calling that a
          failure would be wrong, because most of the work landed; calling it a
          success would hide the casualties. A collection is almost never
          perfectly clean, so <em>partial is the normal state of the world</em>{" "}
          and only worth investigating when the failed count climbs.
        </li>
        <li>
          <strong>failed</strong> &mdash; nothing succeeded. For a publish run,
          that usually means the database could not be reached at all.
        </li>
        <li>
          <strong>running</strong> &mdash; still going, or stopped so abruptly it
          never got to write down how it ended. A run stuck as
          &ldquo;running&rdquo; for hours means the process was killed.
        </li>
        <li>
          <strong>skipped</strong> &mdash; shown against a source that is switched
          off in the configuration. It does not count towards the run&rsquo;s
          outcome either way.
        </li>
      </ul>
      <p>
        The rule combining them is mechanical: all parts ok makes the whole run
        ok, no part ok makes it failed, and anything in between is partial.
      </p>

      <h3>If the Publisher has not reported</h3>
      <p>
        That one symptom covers four different problems: the Pi is off, its
        network is down, the database is unreachable, or the timer stopped. Work
        through them on the Pi.
      </p>
      <pre className="status-guide-code">
        <code>{`systemctl list-timers 'fetchlinks-*'
systemctl status fetchlinks-publish.service
journalctl -u fetchlinks-publish.service -n 200 --no-pager
${PY} ~/fetchlinks/ingest/publish_tool.py \\
    --config ${RUNTIME}/config/fetchlinks.toml status`}</code>
      </pre>
      <p>
        <code>publish_tool.py status</code> prints the local queue even when the
        database is unreachable, which is the situation it is most useful in. A
        queue that is growing alongside a database error means the posts are safe
        on disk and will publish when the connection returns. Nothing is lost by
        waiting.
      </p>

      <h3>If the Collector has stopped but the Publisher is fine</h3>
      <p>
        Collection is failing on its own. The source cards say which of the four
        sources failed and why, in one word: network, timeout, authentication,
        rate limit, HTTP, invalid response, parse or unknown.
      </p>
      <pre className="status-guide-code">
        <code>{`systemctl status fetchlinks-collect.service
journalctl -u fetchlinks-collect.service -n 200 --no-pager
ls -la ${RUNTIME}/outbox/ready | tail`}</code>
      </pre>
      <p>
        An authentication failure on Reddit, Bluesky or Mastodon almost always
        means an expired credential in{" "}
        <code>{RUNTIME}/config/fetchlinks.toml</code>. A rate limit means backing
        off, not fixing. A failing RSS feed is one feed, not the collector:{" "}
        <strong>RSS feeds</strong> in Flightdeck lists exactly which.
      </p>

      <h3>If the queue is growing</h3>
      <p>
        Batches wait in <code>ready</code> until a publish drains them. One or two
        between hourly runs is normal. A count that climbs across several hours
        means publishing is failing, so read the Publisher card first. A batch in{" "}
        <code>failed</code> was quarantined: it did not pass its own integrity
        check and was set aside on purpose so that everything else could publish.
      </p>
      <pre className="status-guide-code">
        <code>{`${PY} ~/fetchlinks/ingest/spool_tool.py --runtime ${RUNTIME} status
${PY} ~/fetchlinks/ingest/spool_tool.py --runtime ${RUNTIME} list failed
${PY} ~/fetchlinks/ingest/spool_tool.py --runtime ${RUNTIME} show <batch-id>
${PY} ~/fetchlinks/ingest/spool_tool.py --runtime ${RUNTIME} verify <batch-id>`}</code>
      </pre>

      <h3>If disk space is low</h3>
      <p>
        The spool keeps published batches for two weeks so a bad publish can be
        replayed. If space is short, shortening that is the first move &mdash;{" "}
        <code>--prune-days</code> on the publish command. Logs are the journal&rsquo;s
        problem and are capped already.
      </p>

      <h3>Where the full logs are</h3>
      <p>
        On the Pi, in the system journal, deliberately. This page carries counts
        and one short reason per run. Full output would mean paying to store it in
        a 0.5 GB database and would create somewhere for a credential to be pasted
        by accident.
      </p>
      <pre className="status-guide-code">
        <code>{`journalctl -u fetchlinks-collect.service --since '6 hours ago' --no-pager
journalctl -u fetchlinks-publish.service --since today --no-pager
journalctl -u fetchlinks-retain.service --since '30 days ago' --no-pager`}</code>
      </pre>

      <h3>What this page deliberately does not cover</h3>
      <ul className="status-guide-list">
        <li>
          <strong>Web requests, errors and page speed.</strong> Vercel&rsquo;s
          dashboard already shows these, including an hour of runtime logs.
          Repeating them here would need a database write on every request or an
          API credential, and would say nothing about the Pi.
        </li>
        <li>
          <strong>Remaining database allowance.</strong> The size shown here is
          the production database&rsquo;s own logical size. Neon&rsquo;s free
          limit is counted per project across every branch, which no query on one
          branch can see. Neon&rsquo;s dashboard is the only authority on how much
          room is left.
        </li>
        <li>
          <strong>Alerts.</strong> There are none, by choice. No new posts on the
          front page is the visible symptom of every failure that matters, and
          this page exists to explain that symptom rather than to page anyone.
        </li>
      </ul>

      <h3>Deploying a change to the Pi</h3>
      <p>Nothing on the Pi updates itself. It is one command, by hand:</p>
      <pre className="status-guide-code">
        <code>cd ~/fetchlinks &amp;&amp; git pull &amp;&amp; ./deploy/bootstrap.sh</code>
      </pre>
    </section>
  );
}
