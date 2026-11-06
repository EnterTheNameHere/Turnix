# file: first-party/applications/experimentRunner/procedureFormat.py ; version: 1
"""Closed Classic procedure contracts and conservative budgets, safe for offline use."""
from __future__ import annotations

import importlib
import re

schema = importlib.import_module("first-party.applications.experimentRunner.definition")
PREFIX = "actant.experiment."
PLATFORM_REF = "c99dd9315b9a8170f036df7d84a0a4a463966548"
MAXIMA = dict(maxSteps=64, maxInputs=16, inputBytes=262144, totalInputBytes=1048576,
    maxArtifacts=8, artifactBytes=262144, maxCliCalls=16, argvBytes=8192, streamBytes=262144,
    messageBytes=262144, definitionDepth=32, definitionNodes=16384)
RECIPES = {
    "ruff-safe-fix": ("ruff", ["check", "--isolated", "--no-cache", "--fix", "--no-unsafe-fixes",
        "--select", "E4,E7,E9,F", {"artifact": True}], [0, 1], True),
    "ruff-format": ("ruff", ["format", "--isolated", "--no-cache", {"artifact": True}], [0], True),
    "ty-check": ("ty", ["check", "--isolated", "--output-format", "concise", {"artifact": True}], [0, 1], False),
    "ruff-check": ("ruff", ["check", "--isolated", "--no-cache", "--select", "E4,E7,E9,F",
        {"artifact": True}], [0, 1], False)}


def isProcedure(value):
    """Select only the explicit new definition identifier, never inferred shape."""
    return type(value) is dict and value.get("schemaId") == PREFIX + "definition@3"


def policy(config):
    """Project validated App controls without transferring process executable selection."""
    return dict(aliases=config["aliases"], **config["limits"], procedureLimits=config["procedureLimits"],
        approvedCommands=config["approvedCommands"], observationCalls=len(config["observation"]["calls"])
        if config["observation"]["enabled"] else 0)


def tree(value, depth=0, counter=None, *, maxDepth=32, maxNodes=16384):
    """Bound node count/depth before canonicalization of direct Python caller values."""
    counter = [0] if counter is None else counter
    counter[0] += 1
    schema.require(depth <= maxDepth and counter[0] <= maxNodes, "Definition tree exceeds bound.")
    if type(value) is dict:
        for key, item in value.items():
            text(key, schema.MAX_BYTES)
            tree(item, depth + 1, counter, maxDepth=maxDepth, maxNodes=maxNodes)
    elif type(value) is list:
        for item in value:
            tree(item, depth + 1, counter, maxDepth=maxDepth, maxNodes=maxNodes)
    elif type(value) is str:
        text(value, schema.MAX_BYTES)
    else:
        schema.require(type(value) in (int, float, bool, type(None)), "Unsupported JSON value.")


def text(value, maximum):
    """Require exact scalar UTF-8 without BOM, NUL or surrogate repair."""
    schema.require(type(value) is str and "\x00" not in value and not value.startswith("\ufeff"), "Invalid UTF-8 text.")
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeError:
        raise schema.ValidationError("Invalid UTF-8 text.") from None
    schema.require(size <= maximum, "Text exceeds bound.")
    return size


def path(value):
    """Validate portable relative artifact names including Windows alias hazards."""
    text(value, 128)
    parts = value.split("/")
    schema.require(1 <= len(parts) <= 4, "Invalid artifact path.")
    reserved = {"CON", "PRN", "AUX", "NUL", *("COM" + str(i) for i in range(1, 10)),
                *("LPT" + str(i) for i in range(1, 10))}
    for part in parts:
        schema.require(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", part) is not None
            and part not in (".", "..") and not part.endswith((".", " "))
            and part.split(".")[0].upper() not in reserved, "Invalid artifact path.")


def host(value):
    """Validate host-owned command recipes/caps without resolving or launching tools."""
    limits = value["procedureLimits"]
    schema.fields(limits, MAXIMA)
    for key, maximum in MAXIMA.items():
        schema.number(limits[key], 1, maximum, integer=True)
    tools = value["processTools"]
    schema.require(type(tools) is dict and set(tools) <= {"ruff", "ty"}, "Invalid configured tools.")
    for executable in tools.values():
        text(executable, 32768)
        schema.require(executable.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[/\\]", executable),
                       "Configured tool must be absolute.")
    commands = value["approvedCommands"]
    schema.require(type(commands) is dict and len(commands) <= 4, "Invalid command policy.")
    for name, command in commands.items():
        schema.require(name in RECIPES, "Unsupported command recipe.")
        schema.fields(command, ("tool", "arguments", "timeoutSeconds", "stdoutBytes", "stderrBytes",
                               "cleanupSeconds", "allowedExitCodes", "mutates"))
        tool, arguments, exits, mutates = RECIPES[name]
        schema.require(command["tool"] == tool and command["arguments"] == arguments
            and command["allowedExitCodes"] == exits and type(command["mutates"]) is bool
            and command["mutates"] == mutates and tool in tools, "Invalid command recipe.")
        for key in ("stdoutBytes", "stderrBytes"):
            schema.number(command[key], 1, limits["streamBytes"], integer=True)
        schema.number(command["timeoutSeconds"], 0, 30, exclusive=True)
        schema.number(command["cleanupSeconds"], 0, 5, exclusive=True)
    schema.number(value["archive"]["commandBytes"], 65536, 64 * 1024 * 1024, integer=True)


def validate(value, policy):
    """Freeze every configuration/reference/control before admitting any procedure."""
    schema.fields(policy, ("aliases", "maxTokens", "timeoutSeconds", "maxTurns", "maxOutputCharacters",
        "maxConfigurations", "maxRepetitions", "maxExecutions", "maxTurnCalls", "procedureLimits", "approvedCommands", "observationCalls"))
    schema.require(type(policy["aliases"]) is dict and bool(policy["aliases"]), "Invalid aliases.")
    for alias, selector in policy["aliases"].items():
        schema.name(alias)
        schema.name(selector)
    for key, maximum in (("maxTokens", 4096), ("maxTurns", 16), ("maxOutputCharacters", 65536),
        ("maxConfigurations", 8), ("maxRepetitions", 10), ("maxExecutions", 32), ("maxTurnCalls", 256)):
        schema.number(policy[key], 1, maximum, integer=True)
    schema.number(policy["timeoutSeconds"], 0, 120, exclusive=True)
    schema.number(policy["observationCalls"], 0, 8, integer=True)
    schema.fields(policy["procedureLimits"], MAXIMA)
    for key, maximum in MAXIMA.items():
        schema.number(policy["procedureLimits"][key], 1, maximum, integer=True)
    tree(value, maxDepth=policy["procedureLimits"]["definitionDepth"], maxNodes=policy["procedureLimits"]["definitionNodes"])
    try:
        value = schema.decode(schema.canonical(value))
    except (ValueError, TypeError, OverflowError):
        raise schema.ValidationError("Invalid procedure JSON.") from None
    schema.fields(value, ("schemaId", "experimentId", "mode", "configurations", "repetitions",
                         "errorPolicy", "contextPolicy", "inputs", "procedure"))
    schema.require(isProcedure(value) and value["mode"] == "classic" and value["errorPolicy"] == "stop"
        and value["contextPolicy"] == "fresh-best-effort", "Unsupported procedure policy.")
    schema.name(value["experimentId"])
    limits = policy["procedureLimits"]
    schema.number(value["repetitions"], 1, min(10, policy["maxRepetitions"]), integer=True)
    configurations = value["configurations"]
    schema.require(type(configurations) is list and 1 <= len(configurations) <= min(8, policy["maxConfigurations"]),
                   "Invalid configuration count.")
    units = len(configurations) * value["repetitions"]
    schema.require(units <= min(32, policy["maxExecutions"]), "Too many executions.")
    names = set()
    for config in configurations:
        schema.fields(config, ("name", "profile", "defaults", "allowedOverrides"))
        schema.name(config["name"])
        schema.name(config["profile"])
        schema.require(config["name"] not in names and config["profile"] in policy["aliases"], "Invalid configuration.")
        names.add(config["name"])
        schema.options(config["defaults"], complete=True)
        config["defaults"].setdefault("temperature", .7)
        overrides = config["allowedOverrides"]
        schema.require(type(overrides) is list and all(type(x) is str for x in overrides)
            and len(overrides) == len(set(overrides)) and set(overrides) <= schema.OPTIONS, "Invalid override policy.")
    inputs, steps = value["inputs"], value["procedure"]
    schema.require(type(inputs) is dict and len(inputs) <= limits["maxInputs"], "Invalid inputs.")
    for name, relative in inputs.items():
        schema.name(name)
        text(relative, 1024)
        schema.require(relative and not relative.startswith(("/", "\\")) and ":" not in relative
            and "\\" not in relative and all(p not in ("", ".", "..") for p in relative.split("/")), "Invalid input path.")
    schema.require(type(steps) is list and 1 <= len(steps) <= limits["maxSteps"], "Invalid step count.")
    kinds, artifacts, paths, calls, inference = {}, {}, set(), 0, 0
    pending = False
    for step in steps:
        schema.require(type(step) is dict and type(step.get("op")) is str, "Invalid step.")
        op = step["op"]
        keys = {"message": ("role", "parts"), "inference": (),
                "save": ("inference", "extract", "artifact", "path"), "cli": ("command", "artifact")}
        schema.require(op in keys, "Unsupported step.")
        schema.fields(step, ("id", "op", *keys[op]), ("overrides",) if op == "inference" else ())
        schema.name(step["id"])
        schema.require(step["id"] not in kinds, "Duplicate step.")
        if op == "message":
            schema.require(step["role"] in ("user", "assistant") and type(step["parts"]) is list
                and 1 <= len(step["parts"]) <= 32, "Invalid message.")
            schema.require(3 + sum(1 if type(p) is dict and ("literal" in p or "input" in p) else 2
                for p in step["parts"]) <= 32, "Message reference budget exceeded.")
            for part in step["parts"]:
                schema.require(type(part) is dict, "Invalid message part.")
                if set(part) == {"literal"}:
                    text(part["literal"], limits["messageBytes"])
                elif set(part) == {"input"}:
                    schema.require(type(part["input"]) is str and part["input"] in inputs, "Unknown input.")
                elif set(part) == {"artifact", "revision"}:
                    schema.require(type(part["artifact"]) is str and part["artifact"] in artifacts
                        and part["revision"] == "current", "Unknown artifact.")
                else:
                    schema.fields(part, ("cli",))
                    schema.require(type(part["cli"]) is str and kinds.get(part["cli"]) == "cli", "Unknown CLI result.")
            pending = step["role"] == "user"
        elif op == "inference":
            schema.require(pending, "Inference requires a new ending user message.")
            pending = False
            inference += 1
            schema.options(step.get("overrides", {}))
            for config in configurations:
                schema.require(set(step.get("overrides", {})) <= set(config["allowedOverrides"]), "Override not allowed.")
                options = config["defaults"] | step.get("overrides", {})
                schema.options(options, complete=True)
                schema.require(options["maxTokens"] <= policy["maxTokens"]
                    and options["timeoutSeconds"] <= policy["timeoutSeconds"], "Request exceeds host limits.")
        elif op == "save":
            schema.require(type(step["inference"]) is str and kinds.get(step["inference"]) == "inference"
                and step["extract"] in ("whole-answer", "one-python-fence"), "Invalid save source.")
            schema.name(step["artifact"])
            path(step["path"])
            schema.require(step["artifact"] not in artifacts and step["path"].casefold() not in paths, "Artifact collision.")
            schema.require(not any(step["path"].casefold().startswith(p + "/") or p.startswith(step["path"].casefold() + "/")
                                   for p in paths), "Artifact ancestor collision.")
            artifacts[step["artifact"]] = step["path"]
            paths.add(step["path"].casefold())
        else:
            schema.require(type(step["command"]) is str and step["command"] in policy["approvedCommands"]
                and type(step["artifact"]) is str and step["artifact"] in artifacts, "Invalid CLI reference.")
            calls += 1
        kinds[step["id"]] = op
    schema.require(steps[-1]["op"] == "inference" and not pending
        and 2 <= inference <= min(16, policy["maxTurns"]) and units * inference <= min(256, policy["maxTurnCalls"])
        and calls <= limits["maxCliCalls"] and len(artifacts) <= limits["maxArtifacts"], "Procedure expansion exceeds bounds.")
    return schema.Snapshot(schema.canonical(value))


def escaped(size):
    """Bound a worst-case JSON string including delimiters."""
    return 6 * size + 2


def budget(definition, policy, caps, inputLengths, *, originalBytes=0, observation=False):
    """Reject the complete suite's worst-case history/artifact/queue expansion."""
    value = definition.value()
    limits = policy["procedureLimits"]
    size = sum(inputLengths.values())
    schema.require(set(inputLengths) == set(value["inputs"]) and size <= limits["totalInputBytes"]
        and all(n <= limits["inputBytes"] for n in inputLengths.values()), "Frozen input budget exceeded.")
    history, reserve, queries, bodies = 64, 0, [], []
    output, artifact = 4 * policy["maxOutputCharacters"], limits["artifactBytes"]
    cliBounds = {}
    calls = 0
    for step in value["procedure"]:
        if step["op"] == "message":
            total = sum(text(p["literal"], limits["messageBytes"]) if "literal" in p else
                inputLengths[p["input"]] if "input" in p else artifact if "artifact" in p
                else cliBounds[p["cli"]] for p in step["parts"])
            schema.require(total <= limits["messageBytes"], "Expanded message exceeds budget.")
            reserve += escaped(total) + 8192
            history += escaped(total) + 128
        elif step["op"] == "inference":
            query = history + 4096
            body = 12 * query + 262144
            schema.require(query <= caps["artifactBytes"] and body <= caps["requestBytes"], "Expanded query exceeds budget.")
            queries.append(query)
            bodies.append(body)
            reserve += body + query + escaped(2 * output) + 65536 + caps["partialRecords"] * caps["pendingBytes"]
            history += escaped(output) + 128
        elif step["op"] == "save":
            reserve += escaped(output) + 2 * artifact + 32768
        else:
            command = policy["approvedCommands"][step["command"]]
            schema.require(sum(len(a.encode("utf-8")) if type(a) is str else 1153 for a in command["arguments"])
                           <= limits["argvBytes"], "Expanded argv exceeds budget.")
            raw = command["stdoutBytes"] + command["stderrBytes"]
            rendered = 4096 + escaped(3 * command["stdoutBytes"]) + escaped(3 * command["stderrBytes"])
            cliBounds[step["id"]] = rendered
            reserve += 65536 + raw + rendered + artifact + 32768
            calls += 1
    units = len(value["configurations"]) * value["repetitions"]
    selected = policy.get("observationCalls", min(8, units * len(queries))) if observation else 0
    records = 8 + 4 * units + 2 * units * len(value["procedure"]) + units * calls + units * len(queries) * (caps["partialRecords"] + 8) + 24 * selected
    total = records * caps["frameBytes"] + units * reserve + originalBytes + len(definition.data) + size + units * 262144 + 1048576
    total += 32 * 1024 * 1024 if observation else 0
    command = 4 * (max(bodies) + max(queries) + escaped(2 * output) + 65536) + 65536 + selected * 9 * 2 * 1024 * 1024
    command = max(command, originalBytes + len(definition.data) + size + caps["frameBytes"] + 131072)
    sidecar = units * (len(definition.data) + sum(queries) + len(queries) * escaped(2 * output)
                       + len(value["procedure"]) * 8192 + 262144)
    schema.require(records <= caps["logicalRecords"] and total <= caps["archiveBytes"]
        and sidecar <= schema.MAX_RESULT_BYTES and command <= caps["commandBytes"], "Expanded run exceeds budget.")
    return dict(archiveBytes=total, logicalRecords=records, commandBytes=command, sidecarBytes=sidecar,
                queryBytes=queries, requestBytes=bodies)


def extract(answer, rule, maximum):
    """Extract one exact Python fence body without trimming, repair or code execution."""
    text(answer, 262144)
    result = answer
    if rule == "one-python-fence":
        lines = answer.splitlines(keepends=True)
        fences = [i for i, line in enumerate(lines) if line.rstrip("\r\n").startswith("```")]
        schema.require(len(fences) == 2 and lines[fences[0]].rstrip("\r\n") == "```python"
            and lines[fences[1]].rstrip("\r\n") == "```" and fences[1] > fences[0] + 1, "Ambiguous Python fence.")
        result = "".join(lines[fences[0] + 1:fences[1]])
    schema.require(rule in ("whole-answer", "one-python-fence") and bool(result), "Invalid extraction.")
    text(result, maximum)
    return result.encode("utf-8")


def semanticInputs(entries):
    """Hash logical input IDs/raw bytes, excluding volatile observation/path metadata."""
    import hashlib
    value = [dict(inputId=e["inputId"], bytes=e["rawRef"]["bytes"], sha256=e["rawRef"]["sha256"])
             for e in sorted(entries, key=lambda e: e["inputId"])]
    return hashlib.sha256(schema.canonical(value)).hexdigest()
