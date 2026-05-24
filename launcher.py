# launcher.py
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import json5
import psutil
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


CREATE_NEW_CONSOLE = subprocess.CREATE_NEW_CONSOLE
LLAMA_CPP_PRESETS_FILE = "launcher_llama_cpp_presets.json5"

# Compatibility repair for old string-style preset fields only.
# List-style args should use valid JSON/JSON5 strings, preferably forward slashes.
LOOSE_BACKSLASH_FIX_FIELDS = {
    "path",
    "args",
}


def fixPresetBackslashes(content: str) -> str:
    fieldPattern = "|".join(re.escape(field) for field in LOOSE_BACKSLASH_FIX_FIELDS)
    
    pattern = re.compile(
        rf'("(?P<field>{fieldPattern})"\s*:\s*)"(?P<value>(?:\\.|[^"\\])*)"',
    )
    
    def replaceMatch(match: re.Match[str]) -> str:
        prefix = match.group(1)
        value = match.group("value")
        
        if "\\" not in value:
            return match.group(0)
        
        fixedValue = fixLooseBackslashes(value)
        return f'{prefix}"{fixedValue}"'
    
    return pattern.sub(replaceMatch, content)


def fixLooseBackslashes(value: str) -> str:
    result: list[str] = []
    index = 0
    
    while index < len(value):
        char = value[index]
        
        if char != "\\":
            result.append(char)
            index += 1
            continue
        
        nextChar = value[index + 1] if index + 1 < len(value) else ""
        
        # Preserve only escapes that are useful inside preset command strings.
        # Other JSON escapes such as \\n, \\t, \\r, or \\u1234 are more likely to be
        # accidental Windows-path backslashes in these fields.
        if nextChar in {'"', "\\"}:
            result.append("\\")
            result.append(nextChar)
            index += 2
            continue
        
        # Otherwise treat it as a loose Windows-style backslash and escape it for JSON/JSON5 parsing.
        result.append("\\\\")
        index += 1
    
    return "".join(result)


class Launcher(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Turnix Launcher")

        self.repoRoot = Path(__file__).resolve().parent
        self.turnixProcess: psutil.Popen | None = None
        self.llamaCppProcess: psutil.Popen | None = None

        self.llamaCppPresets = self.loadLlamaCppPresets()
        self.selectedLlamaCppPreset: dict[str, Any] | None = None

        self.initUI()
        self.initProcessChecker()
    
    def loadLlamaCppPresets(self) -> dict[str, dict[str, Any]]:
        try:
            presetsFile = self.repoRoot / LLAMA_CPP_PRESETS_FILE
            with open(presetsFile, "r", encoding="utf-8") as file:
                content = file.read()
            
            fixedContent = fixPresetBackslashes(content)
            parsedContent = json5.loads(fixedContent)
            if not isinstance(parsedContent, dict):
                raise ValueError("Content of launcher_llama_cpp_presets.json5 must be dict.")
            
            presets: dict[str, dict[str, Any]] = {}
            for name, preset in parsedContent.items():
                if isinstance(name, str) and isinstance(preset, dict):
                    presets[name] = preset
            return presets
        
        except FileNotFoundError:
            print(f"{presetsFile} file not found.")
            return {}
        except Exception as err:
            print(f"Error loading {presetsFile} presets. {err}")
            return {}
    
    def initUI(self) -> None:
        layout = QVBoxLayout()

        # --- Turnix ---
        turnixRow = QHBoxLayout()
        turnixRow.addWidget(QLabel("Turnix"))
        turnixRow.addWidget(self.makeButton("Start", self.startTurnix))
        turnixRow.addWidget(self.makeButton("Restart", self.restartTurnix))
        turnixRow.addWidget(self.makeButton("Stop", self.stopTurnix))
        layout.addLayout(turnixRow)

        # --- Llama.cpp ---
        llamaCppRow = QHBoxLayout()
        llamaCppRow.addWidget(QLabel("Llama.cpp"))
        llamaCppRow.addWidget(self.makeButton("Start", self.startLlamaCpp))
        llamaCppRow.addWidget(self.makeButton("Restart", self.restartLlamaCpp))
        llamaCppRow.addWidget(self.makeButton("Stop", self.stopLlamaCpp))
        layout.addLayout(llamaCppRow)
        
        # --- Presets + Verbose ---
        presetRow = QHBoxLayout()
        presetRow.addWidget(QLabel("Model presets"))
        self.modelBox = QComboBox()
        self.modelBox.addItems(self.llamaCppPresets.keys())
        self.modelBox.currentTextChanged.connect(self.selectLlamaCppModel)
        presetRow.addWidget(self.modelBox)

        self.verboseBox = QCheckBox("Verbose Logging")
        presetRow.addWidget(self.verboseBox)
        layout.addLayout(presetRow)

        turnixOptionsRow = QHBoxLayout()
        self.useLlamaCppForTurnixBox = QCheckBox("Turnix uses llama.cpp")
        self.useLlamaCppForTurnixBox.setChecked(True)
        turnixOptionsRow.addWidget(self.useLlamaCppForTurnixBox)
        layout.addLayout(turnixOptionsRow)
        
        self.selectLlamaCppModel(self.modelBox.currentText())
        self.setLayout(layout)

    def makeButton(self, text: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(callback)
        return button

    # --- Utility: Check if processes were closed manually every 1s ---
    def initProcessChecker(self) -> None:
        self.timer = QTimer()
        self.timer.timeout.connect(self.checkProcesses)
        self.timer.start(1000)  # check every 1s
    
    def checkProcesses(self) -> None:
        for name, attr in [
            ("Turnix", "turnixProcess"),
            ("Llama.cpp", "llamaCppProcess"),
        ]:
            proc = getattr(self, attr)
            if proc and proc.poll() is not None:
                print(f"{name} terminal closed manually.")
                setattr(self, attr, None)

    # --- Utility: Kill process tree ---
    def killProcessTree(self, proc: psutil.Popen) -> None:
        try:
            children = proc.children(recursive=True)
        except psutil.NoSuchProcess:
            return
        
        processes = [*children, proc]

        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied as err:
                print(f"Access denied terminating child PID {child.pid}: {err}")
        
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as err:
            print(f"Access denied terminating parent PID {proc.pid}: {err}")
        
        _gone, alive = psutil.wait_procs(processes, timeout=3.0)
        
        for aliveProc in alive:
            try:
                print(f"Killing stubborn process PID {aliveProc.pid}...")
                aliveProc.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied as err:
                print(f"Access denied killing {aliveProc.pid}: {err}")
        
        psutil.wait_procs(alive, timeout=3.0)

    # --- Turnix ---
    def startTurnix(self) -> None:
        try:
            if self.turnixProcess:
                print("Turnix already running.")
                return
            
            pythonEmbedded = self.repoRoot / "python-embedded" / "python.exe"
            if not pythonEmbedded.exists():
                print(f"Embedded Python not found: {pythonEmbedded}")
                return
            
            args = [
                "-m",
                "backend.cli.main",
            ]
            
            if self.useLlamaCppForTurnixBox.isChecked():
                args.extend(["--provider", "llamacpp"])
                
            command = self.buildPowerShellPythonCommand(
                title="Turnix",
                pythonExe=pythonEmbedded,
                args=args,
            )
            cmd = ["pwsh", "-NoExit", "-Command", command]
            
            print("Starting Turnix in visible console...")
            print("Command:", " ".join(cmd))
            self.turnixProcess = psutil.Popen(
                cmd,
                cwd=self.repoRoot,
                creationflags=CREATE_NEW_CONSOLE,
            )
            print(f"Turnix PID: {self.turnixProcess.pid}")
            
        except Exception as err:
            print(f"Error starting Turnix: {err}")

    def restartTurnix(self) -> None:
        print("Restarting Turnix...")
        self.stopTurnix()
        self.startTurnix()

    def stopTurnix(self) -> None:
        try:
            if self.turnixProcess:
                print(f"Stopping Turnix PID {self.turnixProcess.pid} and its children...")
                self.killProcessTree(self.turnixProcess)
                self.turnixProcess = None
        except Exception as err:
            print(f"Error stopping Turnix: {err}")

    # --- Llama.cpp ---
    def selectLlamaCppModel(self, name: str) -> None:
        if not name:
            self.selectedLlamaCppPreset = None
            return
        
        print("Selecting Llama.cpp model: " + name)
        try:
            self.selectedLlamaCppPreset = self.llamaCppPresets.get(name)
        except Exception as err:
            print(f"Error selecting Llama.cpp model: {err}")

    def startLlamaCpp(self) -> None:
        try:
            if self.llamaCppProcess:
                print("Llama.cpp already running.")
                return
            
            if not self.selectedLlamaCppPreset:
                print("No llama.cpp preset selected.")
                return
            
            print("Starting Llama.cpp in visible console with persistent shell...")
            exe = str(self.selectedLlamaCppPreset.get("path", "")).strip()
            
            if not exe:
                print("Selected llama.cpp preset has no path.")
                return
            
            exePath = Path(exe)
            if not exePath.exists():
                print(f"llama.cpp executable not found: {exePath}")
                return
            
            argsValue = self.selectedLlamaCppPreset.get("args", "")
            
            if isinstance(argsValue, str):
                argsText = argsValue.strip()
                args = shlex.split(argsText, posix=False) if argsText else []
            elif isinstance(argsValue, list):
                args = [str(arg) for arg in argsValue]
            else:
                print("Selected llama.cpp preset has invalid args. Expected string or list.")
                return

            if self.verboseBox.isChecked():
                args.append("--verbose")

            argsQuoted = " ".join(self.quotePowerShellArg(arg) for arg in args)
            exeQuoted = self.quotePowerShellArg(exe)
            cmdStr = f'$host.UI.RawUI.WindowTitle = "LlamaCPP"; & {exeQuoted} {argsQuoted}'
            pwshCmd = ["pwsh", "-NoExit", "-Command", cmdStr]

            print("Command:", " ".join(pwshCmd))
            self.llamaCppProcess = psutil.Popen(
                pwshCmd,
                cwd=self.repoRoot,
                creationflags=CREATE_NEW_CONSOLE,
            )
            print(f"Llama.cpp PID: {self.llamaCppProcess.pid}")
            
        except Exception as err:
            print(f"Error starting Llama.cpp: {err}")

    def restartLlamaCpp(self) -> None:
        print("Restarting Llama.cpp...")
        self.stopLlamaCpp()
        self.startLlamaCpp()

    def stopLlamaCpp(self) -> None:
        try:
            if self.llamaCppProcess:
                print(f"Stopping Llama.cpp PID {self.llamaCppProcess.pid} and its children...")
                self.killProcessTree(self.llamaCppProcess)
                self.llamaCppProcess = None
        except Exception as e:
            print(f"Error stopping Llama.cpp: {e}")

    def buildPowerShellPythonCommand(self, *, title: str, pythonExe: Path, args: list[str]) -> str:
        commandParts = [self.quotePowerShellArg(str(pythonExe)), *[self.quotePowerShellArg(arg) for arg in args]]
        return f'$host.UI.RawUI.WindowTitle = "{title}"; & {" ".join(commandParts)}'
    
    def quotePowerShellArg(self, value: str) -> str:
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    
    # --- Graceful exit ---
    def closeEvent(self, event) -> None:
        print("Launcher is closing. Stopping all running processes...")

        try:
            self.stopTurnix()
        except Exception as e:
            print(f"Error stopping Turnix on exit: {e}")

        try:
            self.stopLlamaCpp()
        except Exception as e:
            print(f"Error stopping Llama.cpp on exit: {e}")

        event.accept()


if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        window = Launcher()
        window.show()
        sys.exit(app.exec())
    except Exception as err:
        print(f"Error starting the application: {err}")
