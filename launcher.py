import sys, os, json5, subprocess, psutil, time,re
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QCheckBox,
)
from PyQt6.QtCore import QTimer

CREATE_NEW_CONSOLE = subprocess.CREATE_NEW_CONSOLE

class Launcher(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Turnix Launcher")

        self.backendProcess: psutil.Popen | None = None
        self.electronProcess: psutil.Popen | None = None
        self.llamaCppProcess: psutil.Popen | None = None

        self.llamaCppPresets = self.loadLlamaCppPresets()
        self.selectedLlamaCppPreset = None

        self.initUI()
        self.initProcessChecker()
    
    def loadLlamaCppPresets(self):
        try:
            with open("launcher_llama_cpp_presets.json5", "r", encoding="utf-8") as file:
                content = file.read()

            # Replace single backslashes inside quoted strings that look like Windows paths
            def fix_path_slashes(match):
                path = match.group(1)
                # Only modify if it contains a backslash and a colon (likely Windows path)
                if "\\" in path and ":" in path:
                    return '"' + path.replace("\\", "\\\\") + '"'
                return '"' + path + '"'
            
            fixed_content = re.sub(r'"([^"]*)"', fix_path_slashes, content)
            return json5.loads(fixed_content)
        
        except FileNotFoundError:
            print("launcher_llama_cpp_presets.json5 file not found.")
            return {}
        except Exception as e:
            print(f"Error loading Launcher launcher_llama_cpp_presets.json5 presets. {e}")
            return {}
    
    def initUI(self):
        layout = QVBoxLayout()

        # --- Backend ---
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Backend"))
        row1.addWidget(self.makeButton("Start", self.startBackend))
        row1.addWidget(self.makeButton("Restart", self.restartBackend))
        row1.addWidget(self.makeButton("Stop", self.stopBackend))
        layout.addLayout(row1)

        # --- Electron ---
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Electron"))
        row2.addWidget(self.makeButton("Start", self.startElectron))
        row2.addWidget(self.makeButton("Restart", self.restartElectron))
        row2.addWidget(self.makeButton("Stop", self.stopElectron))
        layout.addLayout(row2)

        # --- Llama.cpp ---
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Llama.cpp"))
        row3.addWidget(self.makeButton("Start", self.startLlamaCpp))
        row3.addWidget(self.makeButton("Restart", self.restartLlamaCpp))
        row3.addWidget(self.makeButton("Stop", self.stopLlamaCpp))
        layout.addLayout(row3)
        
        # --- Presets + Verbose ---
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Model presets"))
        self.modelBox = QComboBox()
        self.modelBox.addItems(self.llamaCppPresets.keys())
        self.modelBox.currentTextChanged.connect(self.selectLlamaCppModel)
        row4.addWidget(self.modelBox)

        self.verboseBox = QCheckBox("Verbose Logging")
        row4.addWidget(self.verboseBox)
        layout.addLayout(row4)

        self.selectLlamaCppModel(self.modelBox.currentText())
        self.setLayout(layout)

    def makeButton(self, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        return button

    # --- Utility: Check if processes were closed manually every 1s ---
    def initProcessChecker(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.checkProcesses)
        self.timer.start(1000)  # check every 1s
    
    def checkProcesses(self):
        for name, attr in [
            ("Backend", "backendProcess"),
            ("Electron", "electronProcess"),
            ("Llama.cpp", "llamaCppProcess"),
        ]:
            proc = getattr(self, attr)
            if proc and not proc.is_running():
                print(f"{name} terminal closed manually.")
                setattr(self, attr, None)

    # --- Utility: Kill process tree ---
    def killProcessTree(self, proc: psutil.Popen):
        try:
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            proc.kill()
        except psutil.NoSuchProcess:
            pass

    # --- Backend ---
    def startBackend(self):
        try:
            if not self.backendProcess:
                print("Starting backend in visible console...")
                cmd = ["pwsh", "-NoExit", "-Command", "uvicorn backend.server:app"]
                print("Command:", " ".join(cmd))
                self.backendProcess = psutil.Popen(cmd, creationflags=CREATE_NEW_CONSOLE)
                print(f"Backend PID: {self.backendProcess.pid}")
            else:
                print("Backend already running.")
        except Exception as e:
            print(f"Error starting backend: {e}")

    def restartBackend(self):
        print("Restarting backend...")
        self.stopBackend()
        self.startBackend()

    def stopBackend(self):
        try:
            if self.backendProcess:
                print(f"Stopping backend PID {self.backendProcess.pid} and its children...")
                self.killProcessTree(self.backendProcess)
                self.backendProcess = None
                time.sleep(0.3)
        except Exception as e:
            print(f"Error stopping backend: {e}")

    # --- Electron ---
    def startElectron(self):
        try:
            if not self.electronProcess:
                print("Starting Electron in visible console...")
                cmd = ["pwsh", "-NoExit", "-Command", "npm run start"]
                print("Command:", " ".join(cmd))
                self.electronProcess = psutil.Popen(
                    cmd, cwd="electron", creationflags=CREATE_NEW_CONSOLE
                )
                print(f"Electron PID: {self.electronProcess.pid}")
            else:
                print("Electron already running.")
        except Exception as e:
            print(f"Error starting electron: {e}")

    def restartElectron(self):
        print("Restarting electron...")
        self.stopElectron()
        self.startElectron()

    def stopElectron(self):
        try:
            if self.electronProcess:
                print(f"Stopping Electron PID {self.electronProcess.pid} and its children...")
                self.killProcessTree(self.electronProcess)
                self.electronProcess = None
                time.sleep(0.3)
        except Exception as e:
            print(f"Error stopping electron: {e}")

    # --- Llama.cpp ---
    def selectLlamaCppModel(self, name):
        print("Selecting Llama.cpp model: " + name)
        try:
            self.selectedLlamaCppPreset = self.llamaCppPresets.get(name)
        except Exception as e:
            print(f"Error selecting Llama.cpp model: {e}")

    def startLlamaCpp(self):
        try:
            if not self.llamaCppProcess and self.selectedLlamaCppPreset:
                print("Starting Llama.cpp in visible console with persistent shell...")
                exe = self.selectedLlamaCppPreset.get("path", "")
                args = self.selectedLlamaCppPreset.get("args", "").split()
                if self.verboseBox.isChecked():
                    args.append("--verbose")

                # Quote exe + args so PowerShell parses them correctly
                args_quoted = " ".join(
                    f'"{a}"' if " " in a or "\\" in a else a for a in args
                )
                cmd_str = f'& "{exe}" {args_quoted}'

                pwsh_cmd = ["pwsh", "-NoExit", "-Command", cmd_str]

                print("Command:", " ".join(pwsh_cmd))
                self.llamaCppProcess = psutil.Popen(
                    pwsh_cmd,
                    creationflags=CREATE_NEW_CONSOLE
                )
                print(f"Llama.cpp PID: {self.llamaCppProcess.pid}")
            else:
                print("Llama.cpp already running or no preset selected.")
        except Exception as e:
            print(f"Error starting Llama.cpp: {e}")

    def restartLlamaCpp(self):
        print("Restarting Llama.cpp...")
        self.stopLlamaCpp()
        self.startLlamaCpp()

    def stopLlamaCpp(self):
        try:
            if self.llamaCppProcess:
                print(f"Stopping Llama.cpp PID {self.llamaCppProcess.pid} and its children...")
                self.killProcessTree(self.llamaCppProcess)
                self.llamaCppProcess = None
                time.sleep(0.3)
        except Exception as e:
            print(f"Error stopping Llama.cpp: {e}")

    # --- Graceful exit ---
    def closeEvent(self, event):
        print("Launcher is closing. Stopping all running processes...")

        try:
            self.stopBackend()
        except Exception as e:
            print(f"Error stopping backend on exit: {e}")

        try:
            self.stopElectron()
        except Exception as e:
            print(f"Error stopping electron on exit: {e}")

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
    except Exception as e:
        print(f"Error starting the application: {e}")
