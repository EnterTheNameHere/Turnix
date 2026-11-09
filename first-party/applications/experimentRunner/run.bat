@echo off
setlocal
rem EDIT THIS FILE IN AN EDITOR. No arguments, menu, pause or interactive prompts.
rem Prefer the private local/run.bat initialized by this Application's setup.bat.
rem The same template works in the Application folder or its local subfolder.
set "APP=%~dp0"
if not exist "%APP%run.py" for %%I in ("%~dp0..") do set "APP=%%~fI\"
if not exist "%APP%run.py" (
    echo Experiment Runner run.py missing. Keep this BAT in the App folder or local/.
    exit /b 2
)
for %%I in ("%APP%../../..") do set "ROOT=%%~fI"
set "PYTHON=%ROOT%\python-embedded\python.exe"
set "RUNNER=%APP%run.py"
set "LOCAL=%APP%local"
set "EXAMPLES=%APP%examples"
if not exist "%PYTHON%" (
    echo Embedded Python missing. Run repository ROOT setup.bat, then Application setup.bat.
    exit /b 2
)
rem Resolve editable relative paths from the Application folder, independent of caller.
cd /d "%APP%"
if errorlevel 1 exit /b 2
rem Always use "%PYTHON%" -X frozen_modules=off "%RUNNER%" before each command below.
rem Quote paths containing spaces. ROOT, APP, LOCAL, EXAMPLES are absolute.
rem Results, host configs, prompts, generated artifacts and content exports are private.
rem Exit codes: run/validate/inspect/recover 0 success or valid prefix, 1 failure.
rem report/compare/export: 0 complete/comparable, 1 unavailable/incomplete, 2 invalid.
rem export-text: 0 published, 1 rejected/failed. No command executes generated code.
rem
rem RUN: --definition PATH --host-config PATH --results DIRECTORY
rem   --definition-format json or json5 (default json); schema chosen in definition.
rem   definition@2 conversation uses host@3/@4; definition@3 Classic uses host@5.
rem   Input files resolve from definition location. Absolute external definitions work.
rem   Run prints experimentId, outcome, result path, archivePath and inspection help.
rem VALIDATE: same inputs as run except no --results; preflight, no model launch.
rem   Model paths must be configured; this is not a universal model health check.
rem Sample materialization validation (edit private local/host.json first):
rem "%PYTHON%" -X frozen_modules=off "%RUNNER%" validate --definition "%EXAMPLES%\python-materialization\definition.json" --host-config "%LOCAL%\host.json"
rem Sample materialization run: exactly two inferences and four approved CLI steps:
rem "%PYTHON%" -X frozen_modules=off "%RUNNER%" run --definition "%EXAMPLES%\python-materialization\definition.json" --host-config "%LOCAL%\host.json" --results "%LOCAL%\results"
rem Sample user experiment (relative input files stay beside this definition):
rem "%PYTHON%" -X frozen_modules=off "%RUNNER%" run --definition "%LOCAL%\experiments\my-definition.json5" --definition-format json5 --host-config "%LOCAL%\host.json" --results "%LOCAL%\results"
rem
rem INSPECT: exactly one of --archive DIRECTORY or --result JSON_FILE.
rem   --unit ID --turn ID optionally select archive rows; --include-content reveals
rem   private content. --result displays the private JSON result directly; selections
rem   and content flag apply only to archives. Default archive inspection hides content.
rem RECOVER: --archive DIRECTORY [--unit ID] [--turn ID] [--include-content].
rem   Offline committed-prefix inspection only; never resumes or repairs the archive.
rem "%PYTHON%" -X frozen_modules=off "%RUNNER%" inspect --archive "PASTE_ARCHIVE_PATH"
rem "%PYTHON%" -X frozen_modules=off "%RUNNER%" recover --archive "PASTE_ARCHIVE_PATH"
rem
rem REPORT: --archive DIRECTORY [--format table or json, default table].
rem   --unit ID and --turn ID may repeat. Default all planned rows, content hidden.
rem   --include-content or --include-observations require JSON and exactly one unit
rem   plus one turn. Content includes answer/reasoning/request and procedure artifacts.
rem "%PYTHON%" -X frozen_modules=off "%RUNNER%" report --archive "PASTE_ARCHIVE_PATH"
rem "%PYTHON%" -X frozen_modules=off "%RUNNER%" report --archive "PASTE_ARCHIVE_PATH" --unit UNIT_ID --turn corrected --format json --include-content
rem
rem COMPARE: --left ARCHIVE --left-unit ID --left-turn ID --right ARCHIVE
rem   --right-unit ID --right-turn ID --mode MODE [--format table or json].
rem   MODE: actant.experiment.answer-text@1, actant.experiment.reasoning-text@1,
rem   actant.experiment.request-body@1. No content/observation/report selections.
rem   --allow-input-differences explicitly permits different frozen inputs/queries;
rem   Equal/NotEqual/NotComparable describe selected evidence, not code quality.
rem "%PYTHON%" -X frozen_modules=off "%RUNNER%" compare --left "ARCHIVE_A" --left-unit UNIT_A --left-turn draft --right "ARCHIVE_B" --right-unit UNIT_B --right-turn draft --mode actant.experiment.answer-text@1
rem
rem EXPORT: --kind report or compare, same selection options as that command,
rem   --output-root DIRECTORY --name NEW_NAME [--format json or csv, default json].
rem   Writes new directory/manifest last; existing exports are never overwritten.
rem   Private content/observations require report JSON with one unit and turn.
rem "%PYTHON%" -X frozen_modules=off "%RUNNER%" export --kind report --archive "PASTE_ARCHIVE_PATH" --format csv --output-root "%LOCAL%\results\exports" --name summary-01
rem
rem EXPORT-TEXT: --archive DIRECTORY --unit ID --output-root DIRECTORY --name NEW_NAME.
rem   Explicit private content export for one unit, all committed stages in order.
rem   Open index.txt, then numbered .txt files: draft answer, extracted source,
rem   post-safe-fix/format source, diagnostics, feedback and corrected answer.
rem   Also retains exact requests and metadata; protect the entire export directory.
rem   Final response stays an answer, not an implicit workspace save or tool recheck.
rem   Valid failed/incomplete prefixes can be exported; missing evidence stays absent.
rem "%PYTHON%" -X frozen_modules=off "%RUNNER%" export-text --archive "PASTE_ARCHIVE_PATH" --unit UNIT_ID --output-root "%LOCAL%\results\exports" --name readable-01
rem
rem EDIT THE LAST COMMAND BELOW to select your experiment or inspection action.
rem Default is the small basic conversation: verify host paths before the longer
rem materialization. Edit local/host.basic.json once with approved server/model paths.
rem No PATH/global Python is used. CLI output and its exit status remain visible.
"%PYTHON%" -X frozen_modules=off "%RUNNER%" run ^
  --definition "%EXAMPLES%\basic-conversation\definition.json" ^
  --host-config "%LOCAL%\host.basic.json" ^
  --results "%LOCAL%\results"
