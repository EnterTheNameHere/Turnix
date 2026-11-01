// file: tests/tools/observationViewer/fixtures.mjs ; version: 1
/** Deterministic v1 wire fixture; contains no runtime, HTTP or real credentials. */
export const PUB = '1'.repeat(32);
export const BASE = 2n ** 60n + 123n;
/** Construct a valid selected-signal fixture beyond JavaScript timestamp precision. */
export function fixture() {
  const scalar = (value, unit) => ({availability: value === null ? 'unavailable' : 'available', value, unit});
  return {
    schemaId: 'actant.observation-publication@1', publicationId: PUB, sessionId: '2'.repeat(32), revision: '1',
    clock: {kind: 'host-process-monotonic', epochId: '3'.repeat(32), nanosecondsEncoding: 'decimal-string'},
    encodings: {integerEvidence: 'decimal-string', ratesAndPercent: 'finite-json-number'}, units: {timestampsAndGaps: 'nanoseconds', streamCharacters: 'unicode-code-points'},
    scope: 'whole-host-hardware/application-selected-stream', sourceLifetime: 'application',
    needs: {memory: true, cpu: true, gpu: true, stream: true},
    retention: {hardwareSamples: '2', streamWindows: '2', hardwareMayBeTruncated: true, hardwareDroppedCount: null},
    sampleIntervalSeconds: 1, windowSeconds: 1, hardwareAvailability: 'as-reported-per-signal', streamAvailability: 'as-reported', collectionStopped: false,
    hardware: [{collectionStartNs: String(BASE + 5n), collectionEndNs: String(BASE + 105n), displayWallTimeNs: String(2n ** 61n + 77n),
      targetIntervalSeconds: 1, actualIntervalSeconds: null,
      cpu: {aggregatePercent: scalar(0, 'percent'), perLogicalCorePercent: [], droppedCoreCount: '0'},
      gpu: {gpuUtilizationPercent: scalar(null, 'percent'), usedVramBytes: scalar('0', 'bytes'), totalVramBytes: scalar('17179869184', 'bytes')},
      memory: {usedBytes: scalar(String(2n ** 55n + 1n), 'bytes'), totalBytes: scalar(String(2n ** 56n), 'bytes')}}],
    stream: {originNs: String(BASE), observedThroughNs: String(BASE + 1000000000n), endNs: null, firstTextNs: null, lastTextNs: null,
      firstTextLatencyNs: null, silenceNs: '1000000000', characterCount: '0', deliveryCount: '0', droppedWindowCount: '4',
      availability: 'available', state: 'active', outcome: null, active: true, released: false,
      windows: [{startNs: String(BASE), endNs: String(BASE + 1000000000n), characterCount: '0', deliveryCount: '0', firstTextNs: null,
        lastTextNs: null, gapCount: '0', gapSumNs: '0', minGapNs: null, maxGapNs: null, partial: false,
        charactersPerSecond: scalar(0, 'unicode-code-points/second')}]},
    correlation: [{startNs: String(BASE), endNs: String(BASE + 1000000000n), hardwareIndices: [0],
      uncoveredIntervalsNs: [[String(BASE), String(BASE + 5n)], [String(BASE + 105n), String(BASE + 1000000000n)]]}],
  };
}
