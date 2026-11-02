# file: tests/first_party/llmDrivers/test_llamaCppSharedRuntime.py ; version: 8
"""Managed sharing migrates to controlled binding lifetime, not raw driver leases."""
from tests.first_party.llmDrivers.test_llamaCppExecutionResources import rig


def test_managed_driver_is_shared_and_lazy_across_application_runs(rig):
    """Actual host/manifest keeps compatible residency until the final drained lease."""
    host, create, _, processes, _, _, _, _, _ = rig
    first, second = create(), create()
    assert not processes
    first.invokeCapability("switch@1", "A")
    assert second.invokeCapability("run@1", "A") == "answer"
    assert len(processes) == 1
    host.closeApplicationRun(first.applicationRun.applicationRunId)
    assert not processes[0].terminated
    host.closeApplicationRun(second.applicationRun.applicationRunId)
    assert processes[0].terminated
