Turnix Assistant Semantic Index workflows
=========================================

Purpose
-------
This directory contains Assistant Semantic Index workflow role prompts.

These files define how a language model thread behaves after a workflow has
already been selected and parameterized.

This file is human-facing orientation only.

This file is not a runnable workflow.

How to run workflows
--------------------
Do not normally copy workflow prompts manually.

To run, prepare, or dry-run an ASI workflow, use:

docs/design/ai/asi/ASI-RUN_WORKFLOW.txt

The workflow runner resolves WORKFLOW_ID through:

docs/design/ai/asi/registry/ASI-WORKFLOW_REGISTRY.txt

Workflow prompts are loaded by the workflow runner according to registry and
load-group rules.

If this README is loaded with a workflow request, do not execute the request
from this README. Load ASI-RUN_WORKFLOW.txt instead.

Workflow prompt files
---------------------
ASI-SUPERVISOR_THREAD_PROMPT.txt
Controls supervisor/workflow-design behavior. Used for reviewing workflow
state, assigning work, accepting or rejecting candidates, and maintaining
workflow/control files when explicitly assigned.

ASI-WORKER_THREAD_PROMPT.txt
Controls document extraction worker behavior. Used for regenerating extraction
records for assigned canonical DOC_ID values.

ASI-AUDIT_THREAD_PROMPT.txt
Controls batch audit behavior. Used for checking extraction records, authority
boundaries, ledger consistency, accepted-review preservation, and index
readiness for a named batch.

ASI-TEST_THREAD_PROMPT.txt
Controls ASI-only routing test behavior. Used to test whether generated ASI
indexes can tell a fresh assistant what to load without repository-search
rescue.

ASI-INFERRED_TERM_REVIEW_PROMPT.txt
Controls inferred-term review behavior. Used to compare generated ASI or
extraction terminology against canonical source documents. This workflow is
cloud-recommended because it benefits from broad programming, schema, API, and
architecture naming knowledge.

ASI-INDEX_GENERATION_WORKER_PROMPT.txt
Controls ASI index generation behavior. Used to generate or update compact ASI
index files from accepted extraction records, audit reports, and accepted review
requirements.

ASI-SOURCE_PATCH_WORKER_PROMPT.txt
Controls approved source-document clarity patch behavior. Used only when a
source-improvement candidate has been explicitly approved for source patching.

Legacy template note
--------------------
Older ASI workflow experiments used copy-paste launch templates.

The redesigned workflow uses ASI-RUN_WORKFLOW.txt and ASI_WORKFLOW_CALL blocks
instead.

This directory intentionally does not provide ASI-THREAD_LAUNCH_TEMPLATES.txt
as a primary new workflow file.

END
---
This file describes ASI workflow prompt files only.

--- file-meta ---
DOC_ID: ASI-WORKFLOWS-README
DOC_FILE: docs/design/ai/asi/workflows/ASI-WORKFLOWS-README.txt
DOC_REV: 1
DOC_GIT: b2cac12
DOC_STATUS: STABLE
-----------------
