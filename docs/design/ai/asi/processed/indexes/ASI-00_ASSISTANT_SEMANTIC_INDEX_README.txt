Turnix Assistant Semantic Index README
======================================

Purpose
-------
The Assistant Semantic Index (ASI) files are compact assistant-facing routing
metadata for Turnix documentation.

They help a fresh assistant decide which canonical Turnix documents to load for
a topic, term, domain, boundary question, or common confusion trap.

Authority warning
-----------------
ASI files are non-authoritative.

ASI files route to canonical documents. They do not replace canonical documents,
restate full normative meaning, or define Turnix semantics by themselves.

For authoritative meaning, load the referenced canonical source document.
If ASI guidance conflicts with a canonical CMR, DD, DA, ODA, or IS document, the
canonical document wins.

Canonical identity rule
-----------------------
Canonical DOC_ID values remain unchanged.

Assistant family aliases are retrieval aids only.
Examples:
- INVARIANTS routes to DA-01.
- PLATFORM-CORE routes to DA-02.
- TRACE routes to DA-03.
- STATE routes to DA-04.
- RPC routes to DA-21.
- RPC-SCHEMA routes to IS-01.
- DIAGNOSTICS-TOOLING routes to ODA-B.

Do not replace DOC_ID values with aliases in normative references.

Initial index set
-----------------
- ASI-00_ASSISTANT_SEMANTIC_INDEX_README.txt
  Explains ASI scope, authority limits, reading order, Batch 1 authority
  ladder, and Batch 1 known traps.

- ASI-01_DOCUMENT_DOMAIN_INDEX.txt
  Maps Batch 1 documents to domains, family aliases, ownership hints, non-
  ownership hints, load-with guidance, and common routing traps.

Planned later ASI files may include term, retrieval-hint, boundary/non-
ownership, and relation indexes.

Current source batch
--------------------
This first-pass ASI set is derived from Batch 1 foundation extraction records:
- DA-01
- DA-02
- DA-03
- DA-04
- DA-21
- IS-01
- ODA-B

Extraction records are also non-authoritative generated metadata. They help ASI
creation but do not replace canonical documents.

Batch 1 authority ladder
------------------------
Use this as routing order when a question mixes layers:

- DA-01: project invariant and CT-D anchor.
- DA-02: platform truth, contract boundary, and implementation-independence
  anchor.
- DA-03: trace evidence substrate and causal proof.
- DA-04: committed-state authority and transaction commit boundary.
- DA-21: abstract RPC, channel, transport, and message-flow boundary.
- IS-01: concrete RPC envelope schema and exact message fields/type values.
- ODA-B: optional developer-facing diagnostic/tooling presentation that
  consumes evidence.

Use pattern
-----------
1. Use ASI files to choose likely canonical documents.
2. Load the canonical documents before answering normative questions.
3. Resolve the layer first when a query combines RPC, trace evidence, state,
   diagnostics, lifecycle, and success/failure words.
4. Load related canonical documents when ASI identifies a cross-document trap.
5. Do not infer definitions from ASI wording alone.
6. Do not auto-load every related document unless the task needs that scope.

Cross-layer routing rule
------------------------
When a question combines RPC, trace evidence, state, diagnostics, and
success/failure, route by owned layer:

- DA-21 for abstract RPC delivery, acknowledgement, request acceptance,
  routing, subscription, cancellation, timeout, and disconnect meaning.
- IS-01 for concrete envelope fields, message type strings, serialization,
  isComplete, final, origin, route, lane, payload, job, and stateUpdate.
- DA-03 for trace semantics, retained evidence, causal links, trace loss, trace
  spans, and trace correlation meaning.
- DA-04 for whether anything became committed authoritative state.
- ODA-B for how optional developer tooling should display, report, filter, or
  explain retained evidence.
- DA-19, DA-23, or DA-28 when the final outcome belongs to Job, adapter, or
  RuntimeHost lifecycle semantics.

Batch 1 high-risk traps to preserve
-----------------------------------
- Trace evidence is not committed authoritative state. Route trace evidence to
  DA-03 and committed-state authority to DA-04.
- RPC state/update delivery and concrete stateUpdate messages do not by
  themselves define committed authoritative state mutation. Route concrete
  schema to IS-01, abstract delivery to DA-21, and committed-state authority to
  DA-04.
- isComplete marks envelope construction completion, not request success,
  operation completion, work completion, Job success, semantic correctness, or
  committed-state mutation. Route concrete field meaning to IS-01 and message-
  flow boundary meaning to DA-21.
- final marks series finality in the concrete envelope; it is not the same as
  isComplete and does not prove work success.
- acknowledgement or ack means receipt or transport/RPC handling only. It does
  not mean request success, Job creation, Job success, adapter success, or
  RuntimeHost operation completion. Route abstract flow to DA-21 and concrete
  envelope fields to IS-01.
- DA-21 owns RPC acceptance/correlation relations only. It does not own Job,
  adapter, or RuntimeHost lifecycle and terminal outcome semantics.
- message delivery does not prove handler execution.
- route, lane, origin, and other envelope metadata do not grant permission,
  authority, activation, adapter execution, or committed-state mutation.
- transport timeout is not Job timeout.
- transport disconnect is not Job cancellation, adapter failure, or RuntimeHost
  failure by itself.
- diagnostic presentation is not authority.
- ODA-B diagnostic tooling consumes evidence and presents reports. It does not
  become authority over trace semantics, committed state, runtime correctness,
  or canonical trace-domain taxonomy.
- ODA-B ERROR/WARNING/INFO tooling severity vocabulary is not automatically the
  same taxonomy as DA-03 trace event level.
- Missing evidence is not evidence of absence. Route diagnostic/report wording
  to ODA-B and trace evidence mechanics to DA-03.
- Persistence protects committed state but does not define state truth.
- Runtime memory is not committed state merely because it is visible, resident,
  active, or in use.

Current generation note
-----------------------
This ASI update uses Batch 1 extraction records, accepted review requirements,
and the Batch 1 cross-document review guidance.

The generated-index ledger should mark these ASI files as needing a fresh
ASI-only retest after this update.

End
---
This file is non-authoritative assistant routing metadata only.

--- file-meta ---
DOC_ID: ASI-00_ASSISTANT_SEMANTIC_INDEX_README
DOC_FILE: docs/design/ai/asi/processed/indexes/ASI-00_ASSISTANT_SEMANTIC_INDEX_README.txt
DOC_REV: 1
DOC_GIT: 0000000
DOC_STATUS: STABLE
-----------------
