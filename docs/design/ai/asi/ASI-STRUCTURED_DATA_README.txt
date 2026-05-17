Turnix Assistant Semantic Index structured data README
======================================================

Purpose
-------
This file explains the structured-data direction for Assistant Semantic Index
outputs.

This file is non-authoritative workflow guidance. It does not define Turnix
semantics.

Canonical Turnix source documents remain normative.

ASI structured data is routing and review metadata.

Design goal
-----------
ASI structured data exists so assistants and local deterministic tools can use
small, bounded, machine-checkable records instead of large monolithic prose
indexes.

The target model is:

- LLMs generate semantic records and decisions.
- Deterministic tooling validates, sorts, deduplicates, defragments, shards, and
  regenerates manifests.
- Local tools may import ASI records into SQLite, FTS, vector stores, or other
  deterministic search layers.
- Physical file location is storage only and must not be treated as semantic
  authority.

Core schemas
------------
Structured ASI records currently use these standalone schemas:

- docs/design/ai/asi/schemas/ASI-EXTRACTED_ITEM_SCHEMA.json
- docs/design/ai/asi/schemas/ASI-AUDIT_DECISION_SCHEMA.json
- docs/design/ai/asi/schemas/ASI-INDEX_CARD_SCHEMA.json
- docs/design/ai/asi/schemas/ASI-TERMINOLOGY_INFERENCE_SCHEMA.json

Source references
-----------------
ASI records must use CMR-native source references whenever possible.

Preferred source locations include:
- DOC_ID
- canonical source file path
- section number
- rule number
- paragraph number or range
- explicit reference marker such as [ref: DA-08 13.5]

ASI must not invent a parallel section-anchor system for existing source
documents.

If a source document needs a better heading or label, record that as a source
clarification or terminology inference proposal. Do not treat the proposed label
as an existing source locator until it is actually added to the source document.

Extracted item kind meanings
----------------------------
The extracted-item schema uses these kind values:

document_domain
  A document-level domain, ownership area, or non-ownership area. Use this for
  what a document owns, primarily covers, or explicitly does not own.

term
  A literal term, field name, phrase, named concept, or vocabulary item that may
  need routing. Same literal term across documents does not automatically mean
  same semantic meaning.

boundary
  A distinction or X-is-not-Y rule. Use this for traps such as stateUpdate is not
  committed state mutation, acknowledgement is not Job success, or trace evidence
  is not authoritative state.

relation
  A meaningful relation between documents, terms, concepts, or authority areas.
  Use this for support, dependency, neighboring authority, load-with, and
  non-equivalence relationships.

alias
  A search or retrieval synonym. Alias does not mean source-defined term unless
  the canonical source explicitly defines or uses it.

source_clarification_observation
  A note that the source document may be ambiguous, under-named, weakly worded,
  missing a useful boundary, or worth improving. This is not source patch
  approval.

implementation_hint
  A hint that the item affects implementation, schemas, code, command surfaces,
  adapters, or runtime behavior. This does not become implementation authority by
  itself.

other
  Fallback for useful extracted material that does not fit current categories.
  Use sparingly. If many other items appear, the schema likely needs a new kind.

Record kinds
------------
Extracted item
~~~~~~~~~~~~~~
Record type:

extracted_item

Written by:

Extractor

Purpose:

A document-local item found while processing one canonical source document.

An extracted item is a candidate for audit and possible indexing by virtue of
existing. It does not need a candidateForIndex boolean.

Extracted items may include source-clarification hints, but they do not approve
source changes.

Audit decision
~~~~~~~~~~~~~~
Record type:

audit_decision

Written by:

Batch Auditor or Cross-Batch Auditor

Purpose:

A decision or recommendation about one or more extracted items.

Audit decisions do not mutate extracted items. The presence of an audit decision
is how later stages know that an item was reviewed.

Useful decision values include:
- accept_for_index
- accept_as_alias
- accept_as_boundary
- accept_as_relation
- reject_noise
- reject_duplicate
- split_contexts
- merge_with_existing
- source_clarification_candidate
- terminology_inference_candidate
- needs_supervisor_decision
- defer

Index card
~~~~~~~~~~
Record type:

index_card

Written by:

Indexer or deterministic card-generation tooling

Purpose:

A runtime routing card used by assistants or local search tools.

Index cards are not authority. They route to canonical documents and carry
warnings about non-authority, confusable terms, and load order.

Initial card kinds include:
- document
- term_literal
- term_context
- boundary
- relation
- alias
- implementation_hint

Terminology inference
~~~~~~~~~~~~~~~~~~~~~
Record type:

terminology_inference

Written by:

TerminologyInferrer

Purpose:

A proposed useful term, name, source heading, boundary phrase, or relation name
that may not literally appear in source but is strongly implied or useful for
retrieval.

TerminologyInferrer does not approve terminology and does not patch source
files.

Supervisor or later approved workflows decide whether a terminology inference
becomes:
- source terminology
- ASI-only alias
- boundary phrase
- relation name
- source heading
- rejected item
- deferred item

Workflow ownership
------------------
Extractor
~~~~~~~~~
Reads:
- one canonical source document
- batch registry
- accepted extraction requirements when applicable
- existing extraction for the same DOC_ID when needed

Writes:
- extracted_item records for the assigned DOC_ID
- source clarification observations when found

Does not write:
- audit decisions
- final index cards
- canonical source documents
- global manifests

Batch Auditor
~~~~~~~~~~~~~
Reads:
- extracted items for one batch
- relevant batch cross-document material
- existing audit decisions for that batch when needed

Writes:
- audit_decision records
- batch audit report
- batch cross-document findings

Does not write:
- canonical source documents
- final index cards unless explicitly assigned
- extracted items, except through separate correction/replacement workflows

TerminologyInferrer
~~~~~~~~~~~~~~~~~~~
Reads:
- canonical source document
- extracted items
- audit decisions
- generated ASI material when assigned

Writes:
- terminology_inference records

Does not write:
- canonical source documents
- final index cards as accepted truth
- audit decisions

Indexer
~~~~~~~
Reads:
- extracted items
- audit decisions
- accepted supervisor decisions when present
- terminology inferences accepted for index use
- current card shards or proposals when needed

Writes:
- index_card records or card proposal records
- index-generation report or ledger records when assigned

Does not write:
- canonical source documents
- extracted items
- audit decisions
- source patch approvals

Supervisor
~~~~~~~~~~
Reads:
- all ASI workflow/control files as needed
- extracted items
- audit decisions
- terminology inferences
- index cards/proposals
- reconciliation reports

Writes when explicitly assigned:
- workflow/control files
- accepted supervisor decisions
- source-improvement candidate decisions
- terminology acceptance/rejection decisions
- schema changes

Does not directly patch generated extraction or index content as a substitute for
assigning the proper workflow.

SourcePatcher
~~~~~~~~~~~~~
Reads:
- approved source-improvement or terminology decisions
- canonical source documents
- CMR and document-edit guidance

Writes:
- canonical source documents only with explicit approval
- applied-patch status records or reports

Does not write:
- index cards
- extracted items
- audit decisions

Same literal term, different meaning
------------------------------------
Literal terms and contextual meanings are separate concepts.

The index-card schema uses:

term_literal
  A literal spelling such as isRequired.

term_context
  A context-specific meaning of that literal spelling, such as isRequired in
  DA-11 PackRequest grammar or isRequired in an implementation command schema.

Default rule:

Same normalized key in different documents must not be merged as the same meaning
without audit or supervisor decision.

A term_literal card may point to multiple term_context cards through
contextCards.

Example card IDs:

- term:isrequired
- termctx:isrequired:DA-11:packrequest-grammar
- termctx:isrequired:IS-04:runtimehost-command-schema

Append, audit, index
--------------------
Do not make every workflow mutate the same JSON object.

Preferred flow:

1. Extractor writes extracted_item records.
2. Auditor writes audit_decision records about extracted items.
3. TerminologyInferrer writes terminology_inference records when assigned.
4. Supervisor writes accepted decisions only when needed.
5. Indexer writes index_card records from extracted items and decisions.
6. Deterministic tooling validates, sorts, deduplicates, defragments, shards,
   and regenerates manifests.

Derived state examples:

- audited means an audit_decision exists for the extracted item.
- should index means the latest applicable decision accepts the item for index
  use.
- rejected means the latest applicable decision rejects or supersedes the item.

Avoid mutable booleans such as audited or shouldIndex on extracted items unless a
future deterministic tool explicitly derives and exports those fields.

Deterministic organization
--------------------------
LLMs do not own physical shard placement.

LLMs may write proposal records or role-owned output files.

Deterministic tooling owns:
- validation
- sorting
- deduplication
- record movement between shards
- shard splitting
- manifest regeneration
- generated lookup hints

Record movement between files is storage maintenance, not semantic change.

A record's stable ID and payload define its meaning, not the file path that
currently contains it.

Sharding expectation
--------------------
Operational ASI files should remain small enough to be fetched completely by the
GitHub connector.

Large monolithic mutable files are discouraged.

If an operational file approaches unsafe fetch size, split it by a stable and
deterministic key, such as:
- batch
- record type
- semantic family
- normalized-key range

Generated human reports may be larger, but operational structured records should
prefer bounded shards.

Local database use
------------------
Local tools may import ASI JSONL records into SQLite, FTS, vector stores, or
other deterministic stores.

The database or local tool may provide search_index-style behavior for local LLMs.

Cloud assistants should not depend on live access to the local database.

Cloud workflows should work from Git-visible files, compact proposals, compact
reconciliation reports, and bounded shards.

End
---
This file explains ASI structured-data workflow only.

--- file-meta ---
DOC_ID: ASI-STRUCTURED_DATA_README
DOC_FILE: docs/design/ai/asi/ASI-STRUCTURED_DATA_README.txt
DOC_REV: 2
DOC_GIT: 0000000
DOC_STATUS: DRAFT
-----------------
