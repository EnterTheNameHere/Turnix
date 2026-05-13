Turnix Assistant Semantic Index
===============================

Purpose
-------
The Turnix Assistant Semantic Index helps language model assistants decide what
Turnix documents, sections, implementation files, workflow prompts, or processed
index files should be loaded for a task.

ASI is a retrieval and workflow aid.

ASI is not authoritative for Turnix semantics.

For authoritative Turnix meaning, load the referenced canonical source document
resolved through CMR-04.

Directory layout
----------------
ASI files live under:

docs/design/ai/asi/

Primary subdirectories:

registry/
Contains ASI registries, load groups, workflow contracts, file index entries,
and batch definitions.

workflows/
Contains role prompts for ASI workflow threads.

processed/
Contains workflow-maintained outputs and state such as generated indexes,
extraction records, batch reviews, inferred reviews, ledgers, and candidates.

Primary entrypoints
-------------------
Use this file for orientation only.

Do not run workflows from this README.
Do not ask indexes for load guidance from this README.

For workflow invocation, load:

docs/design/ai/asi/ASI-RUN_WORKFLOW.txt

For read-only index load queries, load:

docs/design/ai/asi/ASI-ASK_INDEX_FOR_LOADS.txt

For invocation syntax, load:

docs/design/ai/asi/ASI-WORKFLOW_CALL_FORMAT.txt

Human workflow entrypoint
-------------------------
Use ASI-RUN_WORKFLOW.txt when you want to run, prepare, or dry-run a declared
ASI workflow.

Example:

ASI_WORKFLOW_CALL
WORKFLOW_ID: asi.document.extract
BRANCH: CHATGPT_EXTRACT_INDEX
DOC_ID: DA-03
BATCH_ID: Batch 1 foundation
EXECUTION_TARGET: new_thread_launch_prompt
END_ASI_WORKFLOW_CALL

Assistant load-query entrypoint
-------------------------------
Use ASI-ASK_INDEX_FOR_LOADS.txt when the assistant needs to know what canonical
material to load for a query, term, topic, file, or task.

Example:

ASI_LOAD_QUERY
QUERY: What are fields of TraceSpan?
TASK_CONTEXT: implementing trace serialization
CURRENT_FILE: backend/tracing/trace_span.py
END_ASI_LOAD_QUERY

README action handling
----------------------
If this README is loaded with an action request, do not execute the action from
this file.

If the user asks to run or prepare an ASI workflow, load ASI-RUN_WORKFLOW.txt.

If the user asks what to load for a term, topic, query, file, or task, load
ASI-ASK_INDEX_FOR_LOADS.txt.

If the user asks what ASI is, summarize this README.

Authority boundary
------------------
ASI metadata may guide loading and workflow selection.

ASI metadata may not define Turnix semantics.

ASI processed outputs may not override canonical CMR, DD, DA, ODA, or IS source
documents.

Generated output boundary
-------------------------
Generated or workflow-maintained files under processed/ are updated by declared
ASI workflows.

Humans and assistants may inspect processed files.

Workflow-generated files should not be manually refined as a substitute for
rerunning the appropriate workflow unless the user explicitly chooses manual
repair.

Conflict handling
-----------------
If ASI workflow/control files disagree about permissions, required loads,
required parameters, role authority, generated-output boundaries, or execution
behavior, stop and report the conflict.

Do not silently choose one rule and continue.

END
---
This file is ASI orientation only.

--- file-meta ---
DOC_ID: ASI-README
DOC_FILE: docs/design/ai/asi/ASI-README.txt
DOC_REV: 3
DOC_GIT: fff4fe0
DOC_STATUS: STABLE
-----------------
