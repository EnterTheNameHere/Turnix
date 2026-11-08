# file: first-party/applications/experimentRunner/procedure.py ; version: 2
"""Linear procedures using only invocation-mediated model/process/workspace authority."""
from __future__ import annotations

from contextlib import nullcontext
from functools import partial
import importlib
from pathlib import Path
import sys
import time

from backend.llm.executionSessions import FreshContext
from backend.llm.structuredMessages import LlmMessage, LlmMessages
from backend.orchestration.cancellation import ExecutionCancelled

entry = importlib.import_module("first-party.applications.experimentRunner.codeEntry")
pf = importlib.import_module("first-party.applications.experimentRunner.procedureFormat")
fmt = importlib.import_module("first-party.applications.experimentRunner.archiveFormat")


def archiveFailure(port, error, primary):
    """Poison required evidence independently, preserving the first exact operation exit."""
    first = error if primary is None else primary
    try:
        port.writer.rememberPrimary(first)
        # A secondary critical diagnostic must not become the writer's first exit.
        # The writer retains its own fixed, content-free failure independently.
        port.writer.fail(first)
    except BaseException:
        raise first
    return first


def message(port, step, revisions, cli):
    """Resolve authored parts exclusively from frozen inputs and acknowledged outputs."""
    parts, chunks, refs = [], [], []
    for index, source in enumerate(step["parts"]):
        if "literal" in source:
            raw = source["literal"].encode("utf-8")
            ref = port.put(raw)
            part = dict(kind="literal", partIndex=index, textRef=ref)
            refs.append(ref)
        elif "input" in source:
            raw = port.inputData[source["input"]]
            ref = port.run.inputRefs[source["input"]]
            part = dict(kind="input", partIndex=index, inputId=source["input"], rawRef=ref)
            refs.append(ref)
        elif "artifact" in source:
            saved = revisions[source["artifact"]]
            raw = saved["raw"]
            part = dict(kind="artifact", partIndex=index, artifactId=source["artifact"],
                revision=saved["value"]["revision"], revisionRef=saved["ref"])
            refs.extend((saved["ref"], saved["value"]["contentRef"]))
        else:
            previous = cli[source["cli"]]
            raw = previous["rendered"]
            ref = port.put(raw)
            part = dict(kind="cli", partIndex=index, opId=source["cli"],
                outcomeRef=previous["ref"], renderedRef=ref)
            refs.extend((previous["ref"], ref))
        chunks.append(raw)
        parts.append(part)
    raw = b"".join(chunks)
    pf.text(raw.decode("utf-8"), port.run.config["procedureLimits"]["messageBytes"])
    content = port.put(raw)
    doc = port.doc("message@1", role=step["role"], origin="authored", contentRef=content)
    return dict(parts=parts, messageRef=doc), [*refs, content, doc], raw.decode("utf-8")


def revision(port, step, observed, previous):
    """Stage exact post-observation bytes with an immutable monotonic revision link."""
    pf.text(observed.data.decode("utf-8", errors="strict"), port.run.config["procedureLimits"]["artifactBytes"])
    content = port.put(observed.data)
    value = dict(artifactId=step["artifact"], revision=0 if previous is None else previous["value"]["revision"] + 1,
        relativePath=observed.relativePath, originOpId=step["id"],
        previousRevisionRef=None if previous is None else previous["ref"], contentRef=content,
        workspaceObservation=observed.snapshot())
    return dict(ref=port.doc("file-revision@1", **value), value=value, raw=observed.data)


def inference(recorder, ctx, session, selector, result, current, history, options, port):
    """Retain exact transport/partial and optional per-call observation boundaries."""
    limit = recorder.policy.value()["maxOutputCharacters"]
    query = LlmMessages(tuple(LlmMessage(item["role"], item["content"]) for item in history))
    current.update(input=history[-1]["content"], status="running", query=dict(formatId=query.toQuery().formatId,
        messages=query.snapshot()), requestedOptions=options, startedTimeNs=time.time_ns())
    started, stage = time.monotonic_ns(), "inference"
    fragments = dict(answer="", reasoning="", partial=True, truncated=False)

    def observe(event):
        """Keep bounded private deltas without promoting a partial answer into history."""
        port.observe(event)
        if event.eventType == "delta":
            for key, text in (("answer", event.text), ("reasoning", event.metadata.get("llamaCppReasoning", {}).get("content", ""))):
                room = max(0, limit - len(fragments["answer"]) - len(fragments["reasoning"]))
                fragments[key] += text[:room]
                fragments["truncated"] |= len(text) > room
            current["partial"] = dict(fragments)
            recorder.publish(result)

    began = False
    try:
        port.begin(current["turnId"], current["query"], options)
        began = True
        observed = port.observationController.begin(ctx, port, current["turnId"]) if port.observationController is not None else None
        with observed if observed is not None else nullcontext() as observation, port.capture() as captured:
            response = ctx.llm.run(providerName="llama.cpp", model=selector, query=query.toQuery(), providerOptions=options,
                context=FreshContext(required=False, retain=False), streamObserver=observe, session=session, observation=observation)
            if captured is not None:
                captured[0] = True
        evidence = entry.evidenceSnapshot(response.executionEvidence)
        current.update(executionEvidence=evidence, cleanup=entry.cleanupSnapshot(evidence))
        reasoning = response.providerMetadata.get("llamaCppReasoning", {}).get("content")
        if len(response.rawText) + len(reasoning or "") > limit:
            raise RuntimeError("Host output bound exceeded.")
        current.update(answer=response.rawText, reasoning=reasoning, native=entry.boundedNative(response.providerMetadata),
            executionProfile=dict(contextWindowTokens=response.executionProfile.contextWindowTokens,
                metadata=entry.boundedProfile(response.executionProfile.metadata)), partial=None)
        if current["cleanup"]["quiescence"] != "confirmed":
            raise RuntimeError("Execution cleanup is not confirmed.")
        current["status"] = "succeeded"
    except BaseException as error:
        port.writer.rememberPrimary(error)
        try:
            evidence = getattr(error, "llmCleanup", None)
            if evidence is not None:
                current["executionEvidence"] = entry.evidenceSnapshot(evidence)
                current["cleanup"] = entry.cleanupSnapshot(current["executionEvidence"])
            current.update(status="cancelled" if isinstance(error, ExecutionCancelled) else "failed", error=entry.safeError(error, stage))
        except BaseException as secondary:
            archiveFailure(port, secondary, error)
        raise
    finally:
        primary = sys.exception()
        failure = primary
        try:
            current.update(endedTimeNs=time.time_ns(), durationNs=time.monotonic_ns() - started)
            if began:
                port.completed(current)
        except BaseException as error:
            failure = archiveFailure(port, error, failure)
        try:
            recorder.publish(result)
        except BaseException as error:
            failure = archiveFailure(port, error, failure)
        if primary is None and failure is not None:
            raise failure


def cliStep(ctx, port, step, saved, command, maximum):
    """Execute one prepared command once and preserve evidence even on interruption."""
    requestRef, outcomeRef, post = None, None, None
    refs, evidence, primary = [], None, None
    reason, capture = "execution-failed", dict(status="unavailable", reason="not-started")
    rendered = None
    stage, archiveFailed = "prepare", False
    try:
        before = ctx.workspace.readBytes(saved["value"]["relativePath"], maxBytes=maximum)
        if before.data != saved["raw"]:
            raise RuntimeError("Artifact changed outside the procedure.")
        arguments = tuple(saved["path"] if type(a) is dict else a for a in command["arguments"])
        cwd = str(Path(saved["path"]).parents[len(saved["value"]["relativePath"].split("/")) - 1])
        pf.text(cwd, 1024)
        pf.schema.require(sum(len(a.encode("utf-8")) for a in arguments) <= port.run.config["procedureLimits"]["argvBytes"],
                          "Expanded argv exceeds bound.")
        ticket = ctx.process.prepare(command["tool"], arguments, workingDirectory=cwd,
            **{k: command[k] for k in ("timeoutSeconds", "stdoutBytes", "stderrBytes", "cleanupSeconds")})
        stage = "archive"
        requestRef = port.put(fmt.canonical(ticket.request.snapshot()), json=True)
        try:
            evidence = ctx.process.execute(ticket, beforeLaunch=partial(port.admitted, step["id"]))
        except BaseException as error:
            primary = error
            port.writer.rememberPrimary(error)
            evidence = getattr(error, "evidence", None)
            if evidence is None:
                evidence = getattr(error, "processEvidence", None)
        if evidence is not None:
            stage = "archive"
            values = evidence.snapshot()
            values.pop("schemaId", None)
            values.pop("request", None)
            facts, decoded = {}, {}
            for name in ("stdout", "stderr"):
                stream = getattr(evidence, name)
                ref = port.put(stream.data)
                refs.append(ref)
                values[name + "Ref"] = ref
                values.pop(name, None)
                try:
                    decoded[name] = stream.data.decode("utf-8", errors="strict")
                    decoding = "utf8"
                except UnicodeError:
                    decoded[name], decoding = stream.data.decode("utf-8", errors="replace"), "utf8-replaced"
                facts[name] = dict(observedBytes=stream.observedBytes, eof=stream.eof,
                    limitExceeded=stream.limitExceeded, readFailure=stream.readFailure, decoding=decoding)
                values[name + "Facts"] = facts[name]
            outcomeRef = port.doc("process-outcome@1", requestRef=requestRef, **values)
            rendered = fmt.canonical(dict(status=evidence.status, exitCode=evidence.exitCode, **decoded))
            reason = {"completed": "completed", "cancelled": "cancelled", "timeout": "timeout",
                "output-limit": "output-limit"}.get(evidence.status, "execution-failed")
            if evidence.cleanup.disposition != "confirmed":
                reason, capture = "unknown-cleanup", dict(status="unavailable", reason="cleanup-unknown")
            elif evidence.launchState != "not-attempted":
                stage = "post-file"
                try:
                    observed = ctx.workspace.readBytes(saved["value"]["relativePath"], maxBytes=maximum)
                    pf.text(observed.data.decode("utf-8", errors="strict"), maximum)
                except BaseException as error:
                    category = getattr(error, "category", None)
                    capture = dict(status="unavailable", reason="invalid-utf8" if isinstance(error, UnicodeError) else
                        category if category in ("missing", "over-limit", "changed") else "read-failed")
                    reason = "post-file-unavailable"
                    if not isinstance(error, Exception) and primary is None:
                        primary = error
                        port.writer.rememberPrimary(error)
                else:
                    stage = "archive"
                    post = revision(port, step, observed, saved)
                    post["path"] = saved["path"]
                    capture = dict(status="available", reason="observed")
                    refs.extend((post["ref"], post["value"]["contentRef"]))
            if reason == "completed" and evidence.exitCode not in command["allowedExitCodes"]:
                reason = "unexpected-exit"
            if reason == "completed" and not command["mutates"] and post["raw"] != saved["raw"]:
                reason = "execution-failed"
        if primary is not None and reason == "completed":
            reason = "cancelled" if isinstance(primary, ExecutionCancelled) else "execution-failed"
    except BaseException as error:
        if primary is None:
            primary = error
        port.writer.rememberPrimary(primary)
        if stage == "archive":
            archiveFailed = True
            primary = archiveFailure(port, error, primary)
        reason = "cancelled" if isinstance(primary, ExecutionCancelled) else "execution-failed"
    output = None
    if not archiveFailed:
        try:
            output = port.doc("cli-output@1", processRequestRef=requestRef, processOutcomeRef=outcomeRef,
                postRevisionRef=None if post is None else post["ref"], postCapture=capture)
        except BaseException as error:
            primary = archiveFailure(port, error, primary)
            reason = "cancelled" if isinstance(primary, ExecutionCancelled) else "execution-failed"
    return dict(output=output, refs=[] if output is None else [output, requestRef, outcomeRef, *refs], evidence=evidence,
        post=post, reason=reason, primary=primary, rendered=rendered)


def execute(recorder, ctx, runId):
    """Own one Job/session/workspace across a closed linear sequence, with no retry."""
    with recorder.lock:
        if not recorder.pending or recorder.snapshot.value()["runId"] != runId or recorder.snapshot.value()["applicationOutcome"] != "not-started":
            raise RuntimeError("No matching unexecuted invocation.")
        result = recorder.snapshot.value()
        result["applicationOutcome"] = "running"
        recorder.publish(result)
    definition, policy, port = recorder.definition.value(), recorder.policy.value(), recorder.archivePort
    config = definition["configurations"][0]
    selector = policy["aliases"][config["profile"]]
    history, answers, revisions, cli = [], {}, {}, {}
    turns = {t["turnId"]: t for t in result["turns"]}
    session, stage = None, "transition"
    try:
        ctx.cancellation.raiseIfRequested()
        profile = ctx.llm.profile(providerName="llama.cpp", model=selector)
        ctx.llm.transitionProfile(profile)
        stage = "reservation"
        result["reservation"]["acquisition"] = "attempted"
        session = ctx.llm.reserveConversation(profile)
        result["reservation"]["acquisition"] = "acquired"
        for step, diagnostic in zip(definition["procedure"], result["steps"], strict=True):
            port.check()
            ctx.cancellation.raiseIfRequested()
            stage, started, startNs = step["op"], time.time_ns(), time.monotonic_ns()
            refs, output, reason, primary = [], None, "completed", None
            try:
                if stage == "message":
                    intent, refs, content = message(port, step, revisions, cli)
                elif stage == "inference":
                    query = dict(formatId="actant.llm.messages@1", messages=history)
                    options = config["defaults"] | step.get("overrides", {})
                    queryRef, optionsRef = port.put(fmt.canonical(query), json=True), port.put(fmt.canonical(options), json=True)
                    intent = dict(queryRef=queryRef, requestedOptionsRef=optionsRef, attemptId=port.attemptIds[step["id"]])
                    refs = [queryRef, optionsRef]
                elif stage == "save":
                    answerRef = port.put(answers[step["inference"]].encode("utf-8"))
                    intent = dict(inferenceOpId=step["inference"], answerRef=answerRef, extract=step["extract"],
                        artifactId=step["artifact"], relativePath=step["path"])
                    refs = [answerRef]
                else:
                    command = policy["approvedCommands"][step["command"]]
                    saved = revisions[step["artifact"]]
                    commandRef = port.put(fmt.canonical(command), json=True)
                    intent = dict(commandId=step["command"], artifactId=step["artifact"], preRevisionRef=saved["ref"], commandPolicyRef=commandRef)
                    refs = [saved["ref"], commandRef]
                diagnostic.update(status="running", requestRef=port.planned(step, intent, refs))
                refs = []
                if stage == "message":
                    output = port.doc("message-output@1", messageRef=intent["messageRef"])
                    refs = [output, intent["messageRef"]]
                elif stage == "inference":
                    try:
                        inference(recorder, ctx, session, selector, result, turns[step["id"]], history, options, port)
                    except BaseException as error:
                        primary = error
                        reason = "cancelled" if isinstance(error, ExecutionCancelled) else "execution-failed"
                    try:
                        if step["id"] in port.turnMap:
                            turnRef = port.turnMap[step["id"]]
                            output = port.doc("inference-output@1", turnResultRef=turnRef)
                            refs = [output, turnRef, queryRef, optionsRef]
                    except BaseException as error:
                        primary = archiveFailure(port, error, primary)
                        reason = "cancelled" if isinstance(primary, ExecutionCancelled) else "execution-failed"
                elif stage == "save":
                    raw = pf.extract(answers[step["inference"]], step["extract"], policy["procedureLimits"]["artifactBytes"])
                    extractedRef = port.put(raw)
                    output = port.doc("save-output@1", answerRef=answerRef, extractedRef=extractedRef, revisionRef=None)
                    refs = [output, answerRef, extractedRef]
                    path = ctx.workspace.materializeBytes(step["path"], raw)
                    observed = ctx.workspace.readBytes(step["path"], maxBytes=policy["procedureLimits"]["artifactBytes"])
                    if observed.data != raw:
                        raise RuntimeError("Materialized file changed before observation.")
                    saved = revision(port, step, observed, revisions.get(step["artifact"]))
                    saved["path"] = path
                    output = port.doc("save-output@1", answerRef=answerRef, extractedRef=extractedRef, revisionRef=saved["ref"])
                    refs = [output, answerRef, extractedRef, saved["ref"]]
                else:
                    outcome = cliStep(ctx, port, step, saved, command, policy["procedureLimits"]["artifactBytes"])
                    output, refs, reason, primary = (outcome[k] for k in ("output", "refs", "reason", "primary"))
                    evidence = outcome["evidence"]
                    if evidence is not None:
                        result["processBoundary"] = "confirmed" if evidence.cleanup.disposition == "confirmed" else "unknown"
                        if result["processBoundary"] == "unknown":
                            result["workspaceDisposition"] = "retained"
                    elif step["id"] in port.admissions:
                        result.update(processBoundary="unknown", workspaceDisposition="unknown")
            except BaseException as error:
                if primary is None:
                    primary = error
                port.writer.rememberPrimary(primary)
                reason = "cancelled" if isinstance(primary, ExecutionCancelled) else "execution-failed"
            if diagnostic["requestRef"] is not None:
                try:
                    terminal = dict(status="succeeded" if reason == "completed" else "cancelled" if reason == "cancelled" else "failed",
                        reason=reason, startedTimeNs=started, endedTimeNs=time.time_ns(), durationNs=time.monotonic_ns() - startNs,
                        outputRef=output, error=None if reason == "completed" else dict(category=reason, stage=stage if stage != "cli" else "cli-execute"))
                    diagnostic.update(outcomeRef=port.terminal(step, terminal, refs), status=terminal["status"])
                except BaseException as error:
                    primary = archiveFailure(port, error, primary)
            if primary is not None:
                raise primary
            if reason != "completed":
                raise RuntimeError("Procedure step failed.")
            if stage == "message":
                history.append(dict(role=step["role"], content=content))
            elif stage == "inference":
                answers[step["id"]] = turns[step["id"]]["answer"]
                history.append(dict(role="assistant", content=answers[step["id"]]))
            elif stage == "save":
                revisions[step["artifact"]] = saved
            else:
                revisions[step["artifact"]] = outcome["post"]
                cli[step["id"]] = dict(ref=diagnostic["outcomeRef"], rendered=outcome["rendered"])
            recorder.publish(result)
        ctx.cancellation.raiseIfRequested()
        result["applicationOutcome"] = "completed"
    except BaseException as error:
        try:
            if stage == "reservation" and session is None:
                result["reservation"]["acquisition"] = "failed"
            result.update(applicationOutcome="cancelled" if isinstance(error, ExecutionCancelled) else "failed", error=entry.safeError(error, stage))
        except BaseException as secondary:
            archiveFailure(port, secondary, error)
            raise error
        if not isinstance(error, Exception):
            raise
        if isinstance(error, ExecutionCancelled):
            raise ExecutionCancelled("Experiment procedure cancelled.") from None
        raise RuntimeError("Experiment procedure failed.") from None
    finally:
        primary, closeError = sys.exception(), None
        if session is not None:
            try:
                session.close()
                result["reservation"]["close"] = "queued"
            except BaseException as error:
                closeError = error
                result["reservation"]["close"] = "failed"
                result["applicationOutcome"] = "failed"
                if result["error"] is None:
                    result["error"] = entry.safeError(error, "reservation-close")
        try:
            result.update(endedTimeNs=time.time_ns(), durationNs=time.monotonic_ns() - recorder.startedNs)
            recorder.publish(result)
        except BaseException as error:
            archiveFailure(port, error, primary if primary is not None else closeError)
            if primary is None and closeError is None:
                raise
        if closeError is not None and primary is None:
            if not isinstance(closeError, Exception):
                raise closeError
            raise RuntimeError("Conversation reservation close failed.") from None
    return runId
