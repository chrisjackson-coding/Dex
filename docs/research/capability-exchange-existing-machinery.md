# Research: existing Dex machinery for the Capability Exchange

**Question.** Which shipped, local, held, parked, or planned pieces across Dex
Core, DexDiff, and the Malleable Software programme can be reused for the
Capability Exchange? What must be new, and which trust boundaries constrain the
product definition?

**Research date:** 2026-07-31
**Source snapshot:** Dex Core `c18485d6` / released `v1.81.5`; HeyDex website
`b48a9160`; dex-course `61ef86f6`, with two named unmerged design branches noted
below.

## Decision

The Capability Exchange should be a **new product layer assembled from four
existing bodies of design capital**, not a renamed DexDiff, Doctor mode, or
customization migration.

1. Reuse Doctor's honest diagnostic grammar and deterministic-renderer
   separation.
2. Generalize the customization assessor's bounded, sensitivity-aware evidence
   collection into host adapters.
3. Put every adaptation through lifecycle-style preview, fresh approval,
   preconditions, receipt, verification, and rewind.
4. Reuse DexDiff's job-level methodology and browser-review ideas, plus the
   Malleable Software programme's Orientation, Anchor Challenge, 5C teaching
   loop, and separation of learning from application.

The normalized **Job Map, Foundation Capabilities, Evidence Levels, Capability
Card, host-adapter contract, local concierge, and contribution intake are new
domain objects**. Existing modules assume a Dex vault, a Dex release catalog, or
a DexDiff methodology string and cannot honestly represent an arbitrary personal
AI system.

The product boundary should therefore be:

> Diagnose through read-only, least-privilege host adapters; explain every
> finding with its provenance; let the person choose adaptations and
> contributions independently; and allow writes only through a host-specific
> safety contract with an exact fresh approval.

## Status correction before reuse

Release truth matters because the current narrative architecture map contains
several stale entries.

| Machinery | Current status | Consequence for this effort |
| --- | --- | --- |
| Lifecycle service, transaction engine, and ownership contract | **SHIPPED** | Reuse their trust invariants. The service exposes exact preview/execute/rewind operations, while the transaction engine authorizes a whole plan before any write and is crash-safe ([service](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/lifecycle/service.py#L646-L704), [transaction](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/transaction/engine.py#L1-L16)). |
| Dex Doctor | **SHIPPED** | Reuse the diagnostic contract and evidence honesty, not its Dex-specific probes. Doctor separates a deterministic collector from the conversational renderer and keeps `OK`, `OFF`, `BROKEN`, and `UNKNOWN` distinct ([spec](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/docs/dex-doctor-spec.md#L15-L91)). |
| Customization assessment, Capsule, rebuild, activation, and rewind | **SHIPPED in v1.75.1–v1.76.1** | This is production design capital, not local or held work. The changelog says the rebuild went live in v1.76.0 and was routed through the single lifecycle gate in v1.76.1 ([release truth](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/CHANGELOG.md#L320-L340)). The architecture map's “LOCAL rebuild doorway” text is stale. |
| DexDiff command surface | **SHIPPED; current redesign PARKED** | Reuse its job/methodology and review concepts. Do not extend its current direct-write adoption path as the Capability Exchange implementation ([current map](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/docs/architecture/DEX-CORE-MAP.md#L147-L157)). |
| HeyDex DexDiff storage and review source | **CURRENT SOURCE; deployment not verified here** | It supplies a working account-bound browser review pattern, but not the required Capability Card or selective contribution contract. |
| Malleable Software | **PLANNED, not implemented** | `main` says the programme is still in product definition and represents no production course experience ([README](https://github.com/davekilleen/dex-course/blob/61ef86f6420ec8b33109089243a2369f69dc0622/README.md#L39-L46)). Its learning architecture and 5C system are resolved design work on open, unmerged pull-request branches, so they are reusable product decisions rather than shipped components. |
| `/connect` product doorway | **HELD / unavailable** | The engine exists, but the doorway pull request was closed unmerged. Cloud adapters cannot assume a general Dex connection flow. The released changelog still says the doorway is deliberately closed pending security review ([release truth](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/CHANGELOG.md#L357-L366), [closed pull request](https://github.com/davekilleen/Dex/pull/231)). |
| Ritual Intelligence | **PARKED** | Code and tests do not prove a user capability. Its user-facing preview was retracted because nothing invoked it ([release truth](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/CHANGELOG.md#L871-L882)). This is a useful warning for the Capability Map: code presence is not outcome evidence. |

## What can be reused

### 1. Honest diagnostics: reuse the contract

Doctor contributes four durable rules:

- exercise the same path as the real capability;
- distinguish healthy, intentionally off, broken, and could-not-check;
- report instrument failure instead of silently counting it as success; and
- separate deterministic authority from the prose used to explain it.

Its adoption report already refuses to turn missing or unverifiable evidence
into an action, preserves exact authority fields, and keeps recovery read-only
until a separate engine operation is chosen
([Doctor adoption contract](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/docs/dex-doctor-spec.md#L150-L193)).

For the Capability Exchange, reuse this as an **adapter result envelope** and
renderer rule. Do not reuse `OK/OFF/BROKEN/UNKNOWN` as the Capability Map's
Evidence Level. They answer whether a probe or feature is healthy. The agreed
`Verified / Supported / Reported / Unknown` answers how a capability claim is
known. A finding needs both dimensions where relevant.

### 2. Bounded evidence collection: generalize the assessor

The customization assessor is the closest existing technical ancestor of
Diagnosis:

- it assesses entirely in memory and never caches or mutates the target
  ([service](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/customization_migration/service.py#L71-L110));
- it distinguishes complete from partial or unknown evidence rather than
  extrapolating from an incomplete walk
  ([service](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/customization_migration/service.py#L85-L164));
- it has explicit file, byte, dependency, archive, symlink, and secret limits
  ([inventory](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/customization_migration/inventory.py#L38-L88));
- it marks content readable, restricted, excluded, missing, or hash-only and
  records exclusions with guidance
  ([model](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/customization_migration/model.py#L22-L63),
  [model](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/customization_migration/model.py#L164-L211));
- it detects embedded credentials and refuses symlinks before reading
  ([inventory](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/customization_migration/inventory.py#L168-L191),
  [inventory](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/customization_migration/inventory.py#L217-L265)); and
- its model-facing MCP adapter exposes only assessment, preview, bounded status,
  and digest-bound evidence reads
  ([adapter](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/mcp/customization_migration_server.py#L174-L225)).

These are reusable libraries or patterns after extracting a host-neutral core.
The current classifier itself is **not** portable: it recognizes Dex paths,
Dex customization kinds, a Dex release baseline, and a fixed PARA ownership
model.

### 3. Safe adaptation: reuse the invariant, add a host contract

The existing trust stack is the right standard:

- one sanctioned write verdict;
- hard-denied secret and repository paths;
- unclassified paths fail closed;
- the complete plan is authorized before the first byte changes;
- changed targets carry current-byte preconditions;
- exact preview hashes bind execution to what was shown;
- one writer, snapshots, durable receipts, verification, and rewind.

The ownership source explicitly says vault and runtime content are never updated,
hard-denies credentials and key material, and refuses unclassified paths
([ownership contract](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/portable_contract.py#L1-L29),
[hard deny](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/portable_contract.py#L47-L79),
[mutation policy](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/core/portable_contract.py#L355-L420)).

The customization threat model adds the decisive product boundaries:

- vault content is untrusted input;
- the model never holds a write tool;
- a preview hash is an integrity binding, not consent;
- consent is a fresh human act at mutation time; and
- `verified` requires deterministic or user-confirmed provenance, never model
  confidence
  ([threat model](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/docs/customization-migration-threat-model.md#L19-L87)).

Reuse these invariants. Do not route arbitrary hosts directly through today's
`portable_contract.py`: its operation vocabulary and path classes are Dex
specific. Every adaptation-capable host needs an explicit, versioned ownership
and mutation contract. A host without one remains Diagnose-only.

### 4. Job-level exchange and browser review: reuse the product pattern

DexDiff already groups components by the job they serve, describes the
experience rather than copying code, and produces role-, data-, integration-,
and behaviour-aware methodologies
([generator](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/.claude/skills/diff-generate/SKILL.md#L71-L98)).
Publishing is optional and goes through a browser review surface
([generator](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/.claude/skills/diff-generate/SKILL.md#L113-L155)).
Adoption introduces the problem first, inspects the recipient's system, adapts
to their role and connected tools, and previews the plan
([adopter](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/.claude/skills/diff-adopt/SKILL.md#L51-L103),
[adopter](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/.claude/skills/diff-adopt/SKILL.md#L155-L211)).

The current hosted source also provides useful mechanics:

- an account-bound review session with a 30-minute expiry
  ([review session](https://github.com/davekilleen/heydex-website/blob/b48a91603d0d507101baaf3a5bb497bb294950d8/convex/review.ts#L111-L167));
- a browser editor for individual methodology drafts
  ([review edits](https://github.com/davekilleen/heydex-website/blob/b48a91603d0d507101baaf3a5bb497bb294950d8/convex/review.ts#L326-L359)); and
- explicit visibility choices.

Reuse the flow and account-binding concepts, not the schema or publication
semantics. Current DexDiff stores `methodology` as an opaque string and review
sessions as arrays of diffs
([schema](https://github.com/davekilleen/heydex-website/blob/b48a91603d0d507101baaf3a5bb497bb294950d8/convex/schema.ts#L59-L84),
[schema](https://github.com/davekilleen/heydex-website/blob/b48a91603d0d507101baaf3a5bb497bb294950d8/convex/schema.ts#L155-L180)).
Publishing publishes **every** diff in the session
([publisher](https://github.com/davekilleen/heydex-website/blob/b48a91603d0d507101baaf3a5bb497bb294950d8/convex/review.ts#L407-L474)).
Its sanitizer removes executable HTML patterns, not personal data or secrets
([sanitizer](https://github.com/davekilleen/heydex-website/blob/b48a91603d0d507101baaf3a5bb497bb294950d8/convex/sanitization.ts#L1-L26)).
That cannot satisfy “nothing selected by default; choose, inspect, edit, redact,
and approve each use case.”

The current DexDiff adopter also creates skills, folders, templates, hooks,
settings, instructions, and its own log directly
([build and log](https://github.com/davekilleen/Dex/blob/c18485d623f4c8360a1b085962b90deb46fb781d/.claude/skills/diff-adopt/SKILL.md#L217-L275)).
That path predates the single safe door and must not be inherited.

### 5. Programme and pedagogy: reuse the learning journey

Malleable Software `main` already defines the relevant transformation: improve
one real part of the person's current work, turn a successful workflow into
something reusable, and learn through proved outcomes with private-by-default
evidence
([programme](https://github.com/davekilleen/dex-course/blob/61ef86f6420ec8b33109089243a2369f69dc0622/README.md#L1-L37)).

The resolved learning architecture adds:

- an Orientation that establishes an editable role/context brief, inspects
  confirmed capabilities and constraints, and lets the learner choose an Anchor
  Challenge;
- no AI-fluency score;
- role variants that change the problem and evidence, not the underlying
  concept; and
- separate Learning Progress and Application Progress
  ([Orientation](https://github.com/davekilleen/dex-course/blob/cc802ea6874db66c5bb2051d198406220289f0ef/docs/learning-architecture.md#L31-L52),
  [Module and Mission contract](https://github.com/davekilleen/dex-course/blob/cc802ea6874db66c5bb2051d198406220289f0ef/docs/learning-architecture.md#L71-L106)).

The 5C method supplies the concierge's teaching loop: Context, Conversation,
Contradiction, Contract, and Compounding
([5C decision](https://github.com/davekilleen/dex-course/blob/80f55eeaad9055fda32cae5c80f11d139b1f4eca/docs/research/prompt-content-curation-system.md#L12-L36)).
Its content gates also match this product: current-feature claims require
authoritative evidence; exercises need observable proof; external changes and
publishing require preview and approval; and automation may propose but not
silently publish or install
([selection gates](https://github.com/davekilleen/dex-course/blob/80f55eeaad9055fda32cae5c80f11d139b1f4eca/docs/research/prompt-content-curation-system.md#L169-L189),
[content lifecycle](https://github.com/davekilleen/dex-course/blob/80f55eeaad9055fda32cae5c80f11d139b1f4eca/docs/research/prompt-content-curation-system.md#L223-L256)).

These sources should shape the concierge copy and progression. They do not
supply a running course engine or browser application.

## What must be new

### Host-neutral diagnosis

1. **Host Adapter contract.** Declares discoverable roots, explicit read scope,
   denied paths, symlink/archive policy, supported evidence probes, version
   detection, and whether the adapter is Diagnose-only or Adapt-capable.
2. **Job Map.** User-confirmed jobs with outcomes, recurrence, constraints, and
   relevance. Detection proposes jobs; it never enrolls the person in them.
3. **Foundation Capability taxonomy.** A small universal set independent of Dex
   file or command names.
4. **Evidence graph.** Each capability claim links to observations, probes,
   supplied evidence, user report, exclusions, freshness, and the resulting
   Evidence Level. “File exists” is evidence of configuration, not proof of a
   job outcome.
5. **Adapter test fixtures and conformance.** Every adapter needs benign,
   malformed, secret-bearing, oversized, linked, partial, and changing-system
   fixtures, plus proof that Diagnosis makes no writes.

### Selective exchange

6. **Versioned Capability Card.** A closed schema for job, outcome, method,
   prerequisites, constraints, evidence summary, provenance, safety, portability,
   and redactions. It contains no raw source bytes by default.
7. **Local card builder and disclosure manifest.** Builds one candidate per use
   case, shows the exact outbound fields and bytes, starts with nothing selected,
   and lets the person edit or redact each card independently.
8. **Contribution intake.** A new server contract for draft, submit, withdraw,
   moderation, provenance, and Core evaluation. Submission is never automatic
   Core adoption.
9. **Privacy validation.** Structural secret/PII checks and a final exact-payload
   preview are required. DexDiff's prose anonymisation instruction and XSS
   sanitizer are insufficient.

### Safe adaptation and experience

10. **Host-specific mutation contract.** Names what the host owns, what the user
    owns, what can be created or changed, and how preconditions, backup,
    verification, receipts, and rewind work. No contract means no write.
11. **Portable adaptation recipe.** Connects a capability outcome to multiple
    host-specific implementations instead of treating a Dex skill or folder as
    the capability.
12. **Private local concierge.** One command starts a loopback-only browser
    experience with explicit inspection scope and three separate acts:
    Diagnose, Decide, Adapt. Contribution is a fourth optional act, not the
    price of receiving the diagnosis.

## Non-negotiable boundaries

1. **Diagnosis is read-only at the operating-system capability level**, not
   merely by convention. It must not run Doctor's `--heal`, DexDiff adoption, or
   any model-exposed mutator.
2. **User files are hostile input.** Instructions found in the inspected system
   cannot expand scope, approve writes, or cause sharing.
3. **Evidence language is literal.** Directly inspected configuration is not a
   verified job outcome; model inference is never verification; incomplete
   inspection remains partial or unknown.
4. **Fresh consent is per consequence.** Inspection scope, each adaptation, and
   each outbound Capability Card are separate approvals. A preview digest proves
   sameness, not consent.
5. **Local-first means useful offline.** Diagnosis and private recommendations
   must work without an account or contribution.
6. **No arbitrary-host writes through the Dex vault contract.** A new host must
   prove its own ownership and rewind model first.
7. **No general cloud-adapter promise through `/connect`.** That doorway is not
   available. Early cloud support must use separately reviewed official
   connections, exports, selected evidence, or reported evidence.
8. **Built is not capable.** A capability counts only when the relevant user
   outcome has evidence. Parked, unwired, configured-only, and stale machinery
   must remain visibly distinct.

## Implications for the remaining Wayfinder tickets

- **Choose the Capability Exchange product home:** needs a new product boundary
  even if it imports Core libraries; it should not be implemented as a
  DexDiff command.
- **Choose the first host adapter and pilot cohort:** select a host whose read
  scope and ownership model can be proved, not simply the largest installed
  base.
- **Define the Foundation Capability set / Job Map and evidence model:** treat
  health verdict and evidence level as different axes.
- **Define the cross-system Adaptation safety contract:** make “no host contract,
  no write” explicit and inherit the lifecycle threat model.
- **Design the Capability Card and Core intake contract:** do not reuse the
  DexDiff methodology string or all-items publish operation.
- **Prototype the one-command concierge journey:** reuse Orientation and 5C
  pedagogy while keeping Diagnose, Adapt, and Contribute visibly separate.

No additional Wayfinder ticket is required from this research: the newly sharp
questions already exist as the named child tickets above.

## Bottom line

Dex already has most of the **trust grammar** and much of the **concierge
grammar**. It does not yet have the portable capability model or exchange
contract.

Build the Capability Exchange by extracting and composing those grammars:

> Doctor tells the truth; the customization assessor gathers bounded evidence;
> lifecycle protects changes; DexDiff describes a job and opens review;
> Malleable Software helps the person understand and apply it.

Then add the missing host-neutral domain instead of forcing arbitrary personal
AI systems through Dex-shaped files, statuses, or publication machinery.
