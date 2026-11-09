This maintained fixture reauthors the requirements of the November 9 event-log
parser evaluation. It is not a copy of private trial inputs, host configuration,
requests or outputs, and does not promise byte-identical prompts or replies.
Source requirements and observed limitations:
`ai-workflow/knowledge/experiment-runner-materialization-trial-01.txt` and
`ai-workflow/knowledge/experiment-runner-materialization-trial-01-review.txt`
on canonical branch `actant-integration`.

The nine fixed operations are: frozen priming/task message, draft inference,
strict one-Python-fence save, Ruff safe fix, Ruff format, ty check, Ruff check,
current-source plus diagnostic feedback, corrected inference. There are exactly
two inferences and four native CLI operations, one configuration/repetition.
The final answer is retained separately: it is never implicitly saved, executed
or tool-rechecked. Tool diagnostics and a successful Runner outcome are not
evidence of semantic correctness. The reviewed trial's generated requirements
were unfulfilled.

`host.template.json` is structurally valid but contains explicit EDIT_ME paths.
Application setup creates private `local/host.json` with actual root-managed
Ruff/ty paths. Edit authorized server/model paths. The CPU-first template selects
context 16384, token limit 4096, response cap 9216 characters, source cap 16384
bytes, stdout 1024/stderr 256 bytes per CLI. Observation is off. These are
bounded rejection limits, not truncation instructions or a speed guarantee.
For a compatible GPU build and sufficient VRAM, explicitly select appropriate
gpuLayers/kvOffload/flashAttention in the private host; the original trial used
99/true/true. Do not assume those controls fit another machine. No model or server
is downloaded. Run validate before starting the model; the App README and BAT
contain complete commands. Keep the default example selection basic until paths
and local resources have been checked.
