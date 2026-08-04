# Current Doctor and SessionStart health architecture

Status: fact map for Wayfinder ticket [#396](https://github.com/davekilleen/Dex/issues/396),
audited 2026-08-04. This note records the current implementation and its seams for the
next Wayfinder decisions. It does not choose future product behavior or implement code.

## Execution surfaces

The repository wires four independent `SessionStart` commands in
`.claude/settings.json`: the context hook, the release-evidence verifier, the core
orientation hook, and the connection-health checker (`.claude/settings.json:2-22`,
`.claude/hooks/README.md:13-27`). The health-relevant context hook is
`.claude/hooks/session-start.sh`; it does not invoke `core/utils/doctor.py`.

Within that shell hook, the current flow is:

1. Deduplicate repeated injections for five seconds, resolve the current vault from
   `CLAUDE_PROJECT_DIR`, and print the session context header (`.claude/hooks/session-start.sh:6-30`).
2. Detect a launch agent that still contains the stored former vault path. This is a
   read-only warning and sends the user to `/dex-doctor` for repair
   (`.claude/hooks/session-start.sh:32-55`).
3. Run the daily learning-review fallback in the background when its date marker is
   stale (`.claude/hooks/session-start.sh:72-92`).
4. Start a QMD update in the background when its last-update marker is more than one
   hour old; this is silent (`.claude/hooks/session-start.sh:211-233`).
5. Read and render unacknowledged entries from `.logs/error-queue.json`
   (`.claude/hooks/session-start.sh:235-270`).
6. Run `preflight.run_preflight()`, then render MCP failures and queued errors. The
   formatter intentionally emits nothing when all checked servers are healthy
   (`.claude/hooks/session-start.sh:272-299`, `core/utils/preflight.py:182-220`,
   `core/utils/preflight.py:223-299`).
7. Run the once-per-local-day bounded smoke fallback when there is no clean report for
   today. Its stdout is suppressed; only inconclusive or unable-to-complete statuses
   are printed (`.claude/hooks/session-start.sh:301-325`,
   `core/utils/session_health.py:123-230`).
8. Read `System/.smoke-last-run.json` and print only broken journeys
   (`.claude/hooks/session-start.sh:327-349`).
9. Check a separate hard-coded background-job freshness table and point stale or
   never-run jobs to `/dex-doctor` (`.claude/hooks/session-start.sh:360-398`).

The current hook therefore has no single health result, no compact healthy-state
status, and no consumer of `System/.doctor-last-run.json`.

## Doctor authority and execution

The Doctor collector is `core/utils/doctor.py`. The approved v1 specification calls it
the single diagnostic surface, read-only by default, with optional `--deep` and
`--heal` modes (`docs/dex-doctor-spec.md:6-13`, `docs/dex-doctor-spec.md:46-59`). The
on-demand skill runs the collector and renders the report; the v1 specification
explicitly says there are no scheduled or automatic Doctor runs
(`docs/dex-doctor-spec.md:83-95`, `docs/dex-doctor-spec.md:138-143`).

The collector's normalized vocabulary is:

- `OK`, `OFF`, `BROKEN`, and `UNKNOWN` (`core/utils/doctor.py:42`,
  `core/utils/doctor.py:113-139`).
- A check definition has a stable `id`, user-facing `feature`, and probe function
  (`core/utils/doctor.py:95-102`).
- A probe result can also carry a heal, `feature_status`, `user_message`, and internal
  `structured_detail` (`core/utils/doctor.py:104-139`).

The current source registry contains 23 quick checks and 8 deep checks. The exact
coverage is:

**Quick (23):** `vault.structure`, `vault.configs`, `vault.git`, `brain.git`,
`topology.pre-split-archive`, `vault.auto-commit`, `topology.migration-pending`,
`release.catalog`, `adoption.plan`, `smoke.history`, `mcp.registered`, `mcp.orphans`,
`python.env`, `hooks.wired`, `jobs.loaded`, `jobs.fresh`, `preflight.queue`,
`capabilities.rooms`, `entity.engine`, `customizations.skills`, `customizations.mcp`,
`core.drift`, and `doctor.self` (`core/utils/doctor.py:611-655`).

**Deep (8):** `customizations.assessment`, `customizations.migration-status`,
`granola.query_path`, `calendar.access`, `qmd.live`, `integrations.enabled`,
`mcp.importable`, and `smoke.journeys` (`core/utils/doctor.py:657-674`).

`collect()` chooses quick or quick-plus-deep, optionally applies Tier-1 heals, turns
raised probes into `UNKNOWN`, adds the instrument self-check, and emits
`generated_at`, `mode`, `instruments`, `checks`, `summary`, and `adoption`
(`core/utils/doctor.py:976-1096`). It attempts to persist the same report at
`System/.doctor-last-run.json` through the lifecycle transaction service; a write
failure changes `doctor.self` to `BROKEN` in the returned report
(`core/utils/doctor.py:954-974`, `core/utils/doctor.py:1113-1131`).

The serialized per-check contract is narrower than the in-memory result: it includes
`id`, `feature`, `verdict`, `detail`, `heal`, and optional `feature_status` or
`user_message`, but not `structured_detail` (`core/utils/doctor.py:717-729`). Deep
customization structured details are copied into two report-level fields only
(`core/utils/doctor.py:1097-1111`).

## Persisted health-related state

| Artifact | Current owner and meaning |
|---|---|
| `System/.doctor-last-run.json` | Doctor's latest collector report, written after a run (`core/utils/doctor.py:174-176`, `core/utils/doctor.py:954-974`). |
| `.logs/mcp-health.json` | Preflight's cached MCP-server results, refreshed when `.mcp.json` changes, the cache is over 24 hours old, or a newer unacknowledged error exists (`core/utils/preflight.py:29-45`, `core/utils/preflight.py:98-128`, `core/utils/preflight.py:182-220`). |
| `.logs/error-queue.json` | Unacknowledged and historical preflight/application error entries, read both by SessionStart and Doctor (`.claude/hooks/session-start.sh:235-270`, `core/utils/doctor.py:2536-2545`, `core/utils/doctor.py:2681-2751`). |
| `System/.smoke-last-run.json` | Smoke harness report used by SessionStart to surface broken journeys and by the daily fallback to decide whether today's check is clean (`.claude/hooks/session-start.sh:327-349`, `core/utils/session_health.py:46-51`, `core/utils/session_health.py:95-120`). |
| `System/.dex/session-health-success.json` | Local-date success marker for the daily smoke fallback; written atomically after a clean report (`core/utils/session_health.py:20-23`, `core/utils/session_health.py:54-92`, `core/utils/session_health.py:201-213`). |
| `System/.dex/session-health.lock` | Process lock preventing overlapping daily fallback smoke runs (`core/utils/session_health.py:20-23`, `core/utils/session_health.py:186-205`). |
| `System/.last-qmd-update` | SessionStart's timestamp marker for its silent background QMD update (`.claude/hooks/session-start.sh:211-233`). |
| `System/.last-learning-check` | SessionStart's local-date throttle for the learning-review fallback (`.claude/hooks/session-start.sh:79-88`). |
| `.scripts/logs/*.log` | Application logs whose mtimes are used for the four monitored background jobs (`.claude/hooks/session-start.sh:360-398`, `core/utils/doctor.py:591-609`). |

## Background-job ownership and freshness

Doctor discovers only `~/Library/LaunchAgents/com.dex.*.plist`
(`core/utils/doctor.py:2241-2243`). It treats a job as belonging to the checked vault
only when an absolute `ProgramArguments` path resolves beneath that vault; a shared
label is not ownership evidence (`core/utils/doctor.py:2273-2297`). `jobs.loaded` then
checks plist configuration, interpreter executability, `launchctl` load state, and
last exit status (`core/utils/doctor.py:2400-2495`). `jobs.fresh` evaluates the same
attributable jobs against the four thresholds in `JOB_FRESHNESS`
(`core/utils/doctor.py:591-609`, `core/utils/doctor.py:2498-2533`).

SessionStart uses a separate shell table with the same four labels, paths, and
thresholds, but checks whether the named plist file exists and reads its log mtime
without applying Doctor's absolute-`ProgramArguments` ownership test
(`.claude/hooks/session-start.sh:360-398`). The shell also scans both `com.dex.*` and
`com.claudesidian.*` files for the old-path warning (`.claude/hooks/session-start.sh:43-49`).
This is an existing architectural seam, not a new decision: adjacent tracker issues
cover stale-path ownership and user-installed launch-agent observability
([#364](https://github.com/davekilleen/Dex/issues/364),
[#253](https://github.com/davekilleen/Dex/issues/253)).

## Current seams to carry into the next decisions

These are observed facts, not proposed solutions:

1. SessionStart has several independent state readers and output blocks; it does not
   read the Doctor snapshot, and healthy preflight/smoke states are intentionally
   silent. The approved Doctor spec even describes the future session-start surface as
   a later addition (`docs/dex-doctor-spec.md:80-81`, `docs/dex-doctor-spec.md:138-143`).
2. There are multiple freshness notions: preflight's cache age/config/error triggers,
   the daily smoke report plus local-date marker, QMD/learning throttle markers, and
   background-job log thresholds. They are not currently normalized into one result.
3. The source registry is the current complete Doctor coverage: 31 checks total. The
   older v1 spec's JSON example says `attempted: 14`, and its compact registry table
   omits several checks now present in source (`docs/dex-doctor-spec.md:61-77`,
   `docs/dex-doctor-spec.md:97-123`). Future planning should use the code registry as
   the current authority while treating the documentation mismatch as a compatibility
   question.
4. Doctor's serialized check entries do not yet carry a common root cause, dependency,
   impact, evidence, freshness, or Doctor route field. The current common fields are
   `id`, `feature`, `verdict`, `detail`, optional heal, and optional feature/user
   message (`core/utils/doctor.py:717-729`).
5. The current architecture already detects the motivating classes of failure in the
   registry: meeting sync freshness (`jobs.fresh` and `granola.query_path`), semantic
   search (`qmd.live`), and downstream entity state (`entity.engine`)
   (`core/utils/doctor.py:634-642`, `core/utils/doctor.py:657-674`,
   `core/utils/doctor.py:3938-4071`).

## Boundary for the remaining Wayfinder tickets

- #393 should define the normalized domain/reporting vocabulary over the 31-check
  registry and these existing artifacts.
- #395 should decide how a latest complete snapshot is persisted/read and refreshed
  without creating a second diagnostic authority or losing partial/unknown state.
- #394 should define how a user-facing impact routes back to the authoritative Doctor
  check, evidence, and repair path.

No product behavior or architecture was resolved by this note.
