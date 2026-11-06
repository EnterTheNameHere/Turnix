# file: first-party/applications/experimentRunner/results.py ; version: 2
"""Detached initial diagnostics shared by the Application and trusted launcher."""
from uuid import uuid4
import time


def initialResult(definition, runId=None):
    """Allocate one opaque invocation identity with explicit unavailable turn evidence."""
    value = definition.value()
    config = value["configurations"][0]
    return {"schemaId": "actant.experiment.result@2", "runId": uuid4().hex if runId is None else runId,
            "experimentId": value["experimentId"], "definition": value, "definitionSha256": definition.sha256,
            "configurationName": config["name"], "profileAlias": config["profile"],
            "status": None, "applicationOutcome": "not-started", "startedTimeNs": time.time_ns(),
            "endedTimeNs": None, "durationNs": None, "error": None,
            "reservation": {"acquisition": "not-attempted", "close": "not-attempted", "releaseReceipt": "unavailable"},
            "turns": [dict(turnId=turn["id"], input=turn["content"], status="not-started", query=None,
                           requestedOptions=None, answer=None, reasoning=None, native=None, executionProfile=None,
                           executionEvidence=None, cleanup={"status": "unavailable", "quiescence": "unknown"},
                           startedTimeNs=None, endedTimeNs=None, durationNs=None, error=None, partial=None)
                      for turn in value["conversation"]["turns"]]}


def initialSuite(definition, unitDefinition):
    """Allocate bounded ordered units with stable independent identities before admission."""
    value = definition.value()
    units = []
    for index, config in enumerate(value["configurations"]):
        for repetition in range(1, value["repetitions"] + 1):
            unitId = uuid4().hex
            units.append({"unitId": unitId, "index": len(units) + 1, "configurationIndex": index + 1,
                          "configurationName": config["name"], "profileAlias": config["profile"],
                          "repetition": repetition, "status": "not-started", "jobOutcome": None,
                          "lifetimeBoundary": "not-started", "configurationEvidence": None,
                          "run": initialResult(unitDefinition(definition, index), unitId)})
    return {"schemaId": "actant.experiment.suite@1", "suiteId": uuid4().hex,
            "experimentId": value["experimentId"], "definition": value, "definitionSha256": definition.sha256,
            "status": None, "startedTimeNs": time.time_ns(), "endedTimeNs": None, "durationNs": None,
            "units": units}
