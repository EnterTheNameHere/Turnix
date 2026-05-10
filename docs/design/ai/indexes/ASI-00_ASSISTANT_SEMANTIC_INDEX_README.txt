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
  Explains ASI scope, authority limits, reading order, and Batch 1 known traps.

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

Use pattern
-----------
1. Use ASI files to choose likely canonical documents.
2. Load the canonical documents before answering normative questions.
3. Load related canonical documents when ASI identifies a cross-document trap.
4. Do not infer definitions from ASI wording alone.
5. Do not auto-load every related document unless the task needs that scope.

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
- acknowledgement or ack means receipt or transport/RPC handling only. It does
  not mean request success, Job creation, Job success, adapter success, or
  RuntimeHost operation completion. Route abstract flow to DA-21 and concrete
  envelope fields to IS-01.
- DA-21 owns RPC acceptance/correlation relations only. It does not own Job,
  adapter, or RuntimeHost lifecycle and terminal outcome semantics.
- ODA-B diagnostic tooling consumes evidence and presents reports. It does not
  become authority over trace semantics, committed state, runtime correctness,
  or canonical trace-domain taxonomy.
- ODA-B ERROR/WARNING/INFO tooling severity vocabulary is not automatically the
  same taxonomy as DA-03 trace event level.
- Missing evidence is not evidence of absence. Route diagnostic/report wording
  to ODA-B and trace evidence mechanics to DA-03.

Current generation note
-----------------------
This is a first-pass index generated from Batch 1 extraction records and
accepted review requirements.

Known workflow warning: the current Batch 1 audit report identified an invalid
confidence label in ODA-B.extract.txt. The ASI files keep the intended ODA-B
routing and boundary guidance, but Batch 1 should not be marked fully INDEXED in
the workflow ledger until the supervisor accepts indexing despite that warning
or the extraction record is corrected.

End
---
This file is non-authoritative assistant routing metadata only.
