# launcher.py
from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import json5
import psutil
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


CREATE_NEW_CONSOLE = subprocess.CREATE_NEW_CONSOLE
LLAMA_CPP_CONFIG_FILE = "launcher_llama_cpp_config.json5"
LLAMA_CPP_USER_STATE_FILE = "launcher_llama_cpp_user.local.json5"

CACHE_TYPES = [
    "f32",
    "f16",
    "bf16",
    "q8_0",
    "q4_0",
    "q4_1",
    "iq4_nl",
    "q5_0",
    "q5_1",
]

DEFAULT_LLAMA_CPP_OPTIONS: dict[str, Any] = {
    "ctxSize": 32768,
    "flashAttention": "auto",
    "kvOffload": True,
    "cacheTypeK": "f16",
    "cacheTypeV": "f16",
    "mlock": False,
    "mmap": True,
    "cpuMoeLayers": 0,
    "gpuLayers": "auto",
    "verbosity": 3,
    "specialTokens": False,
    "spmInfill": False,
    "host": "127.0.0.1",
    "port": 1234,
    "metrics": False,
    "props": False,
    "slots": True,
}

LOCAL_DEFAULT_LLAMA_CPP_HOST_PORT_OPTIONS = {
    "host": "127.0.0.1",
    "port": 1234,
}

LOCAL_ONLY_OPTION_KEYS = {
    "host",
    "port",
}

LLAMA_CPP_OPTIONS_TOOLTIPS = {
    "ctxSize": "--ctx-size N\n\nSize of the prompt context.\n\n0 = loaded from model.",
    "flashAttention": (
        "--flash-attn [on|off|auto]\n\n"
        "Sets Flash Attention use.\n\n"
        "Values:\n"
        "- on\n"
        "- off\n"
        "- auto"
    ),
    "kvOffload": (
        "--kv-offload / --no-kv-offload\n\n"
        "Whether to enable KV cache offloading.\n\n"
        "Default: enabled."
    ),
    "cacheTypeK": (
        "--cache-type-k TYPE\n\n"
        "KV cache data type for K.\n\n"
        "Allowed values:\n"
        "- f32\n"
        "- f16\n"
        "- bf16\n"
        "- q8_0\n"
        "- q4_0\n"
        "- q4_1\n"
        "- iq4_nl\n"
        "- q5_0\n"
        "- q5_1\n\n"
        "Default: f16."
    ),
    "cacheTypeV": (
        "--cache-type-v TYPE\n\n"
        "KV cache data type for V.\n\n"
        "Allowed values:\n"
        "- f32\n"
        "- f16\n"
        "- bf16\n"
        "- q8_0\n"
        "- q4_0\n"
        "- q4_1\n"
        "- iq4_nl\n"
        "- q5_0\n"
        "- q5_1\n\n"
        "Default: f16."
    ),
    "mlock": (
        "--mlock\n\n"
        "Forces the system to keep the model in RAM rather than swapping or compressing it."
    ),
    "mmap": (
        "--mmap / --no-mmap\n\n"
        "Whether to memory-map the model.\n\n"
        "If mmap is disabled, loading may be slower, but it may reduce pageouts when not using mlock.\n\n"
        "Default: enabled."
    ),
    "cpuMoeLayers": (
        "--n-cpu-moe N\n\n"
        "Keeps the Mixture of Experts (MoE) weights of the first N layers in the CPU."
    ),
    "gpuLayers": (
        "--n-gpu-layers N\n\n"
        "Maximum number of layers to store in VRAM.\n\n"
        "Accepted values:\n"
        "- exact number\n"
        "- auto\n"
        "- all\n\n"
        "Default: auto."
    ),
    "verbosity": (
        "--verbosity N\n\n"
        "Sets the verbosity threshold. Messages with a higher verbosity are ignored.\n\n"
        "Values:\n"
        "- 0: generic output\n"
        "- 1: error\n"
        "- 2: warning\n"
        "- 3: info\n"
        "- 4: trace / more info\n"
        "- 5: debug\n\n"
        "Default: 3."
    ),
    "specialTokens": "--special\n\nEnables special token output.\n\nDefault: false.",
    "spmInfill": (
        "--spm-infill\n\n"
        "Uses Suffix/Prefix/Middle pattern for infill instead of Prefix/Suffix/Middle.\n\n"
        "Some models prefer this.\n\n"
        "Default: disabled."
    ),
    "host": (
        "--host HOST\n\n"
        "IP address to listen on.\n\n"
        "Can also bind to a UNIX socket if the address ends with .sock.\n\n"
        "Default: 127.0.0.1."
    ),
    "port": "--port PORT\n\nPort to listen on.\n\nDefault: 1234.",
    "metrics": (
        "--metrics\n\n"
        "Enables Prometheus-compatible metrics endpoint.\n\n"
        "Default: disabled."
    ),
    "props": (
        "--props\n\n"
        "Enables changing global properties via POST /props.\n\n"
        "Default: disabled."
    ),
    "slots": (
        "--slots / --no-slots\n\n"
        "Exposes slots monitoring endpoint.\n\n"
        "Default: enabled."
    ),
}

@dataclass(frozen=True)
class LlamaCppModel:
    displayName: str
    modelPath: Path
    modelRoot: Path
    relativePath: Path
    profileKey: str


def loadJson5File(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
        parsed = json5.loads(content)

        if isinstance(parsed, dict):
            return parsed
        
        print(f"{path} must contain an object.")
        return fallback
        
    except FileNotFoundError:
        print(f"{path} file not found. Using fallback settings.")
        return fallback
    except Exception as err:
        print(f"Error loading {path}: {err}")
        return fallback


def saveJson5File(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json5.dumps(data, indent=4, allow_duplicate_keys=False, quote_keys=True, ensure_ascii=False))


def normalizedSettingsMap(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(DEFAULT_LLAMA_CPP_OPTIONS)
    normalized.update(settings)
    return normalized


def portableDefaultOptions(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in settings.items()
        if key not in LOCAL_ONLY_OPTION_KEYS
    }


def cleanLlamaCppConfig(config: dict[str, Any]) -> dict[str, Any]:
    defaultOptions = config.get("defaultOptions", {})
    if not isinstance(defaultOptions, dict):
        defaultOptions = {}

    return {
        "defaultOptions": portableDefaultOptions(defaultOptions),
    }


class ProfileNameDialog(QDialog):
    def __init__(self, *, title: str, defaultName: str, parent: QWidget | None = None) -> None:
        print("ProfileNameDialog::__init__")
        super().__init__(parent)
        self.setWindowTitle(title)
        
        layout = QVBoxLayout()
        form = QFormLayout()

        self.nameEdit = QLineEdit()
        self.nameEdit.setText(defaultName)
        self.nameEdit.setCursorPosition(len(defaultName))
        form.addRow("Profile name:", self.nameEdit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.setLayout(layout)
    
    def profileName(self) -> str:
        return self.nameEdit.text().strip()


class Launcher(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Turnix Launcher")

        self.repoRoot = Path(__file__).resolve().parent
        self.turnixProcess: psutil.Popen | None = None
        self.llamaCppProcess: psutil.Popen | None = None

        self.llamaCppConfig = self.loadLlamaCppConfig()
        self.userState = self.loadUserState()
        
        self.llamaCppModels: list[LlamaCppModel] = []
        self.selectedModel: LlamaCppModel | None = None
        self.selectedProfileKind = "default"
        self.selectedProfileName: str | None = None
        self.profileIsModified = False
        self.loadingUi = False

        self.initUI()
        self.initProcessChecker()
        self.refreshModels()
    
    def loadLlamaCppConfig(self) -> dict[str, Any]:
        configFile = self.repoRoot / LLAMA_CPP_CONFIG_FILE
        config = loadJson5File(configFile, {})
        return cleanLlamaCppConfig(config)
        
    def saveLlamaCppConfig(self) -> None:
        configFile = self.repoRoot / LLAMA_CPP_CONFIG_FILE
        self.llamaCppConfig = cleanLlamaCppConfig(self.llamaCppConfig)
        saveJson5File(configFile, self.llamaCppConfig)
    
    def loadUserState(self) -> dict[str, Any]:
        stateFile = self.repoRoot / LLAMA_CPP_USER_STATE_FILE
        state = loadJson5File(stateFile, {})
        state.setdefault("serverPath", "")
        state.setdefault("modelDirectories", [])
        state.setdefault("localDefaultOptions", dict(LOCAL_DEFAULT_LLAMA_CPP_HOST_PORT_OPTIONS))
        state.setdefault("rememberedModelProfiles", {})
        state.setdefault("namedProfilesByModel", {})
        state.setdefault("lastSelectedModelKey", "")

        if not isinstance(state["serverPath"], str):
            state["serverPath"] = ""

        if not isinstance(state["modelDirectories"], list):
            state["modelDirectories"] = []

        localDefaultOptions = state.get("localDefaultOptions")
        if not isinstance(localDefaultOptions, dict):
            localDefaultOptions = dict(LOCAL_DEFAULT_LLAMA_CPP_HOST_PORT_OPTIONS)
            state["localDefaultOptions"] = localDefaultOptions

        for key, value in LOCAL_DEFAULT_LLAMA_CPP_HOST_PORT_OPTIONS.items():
            localDefaultOptions.setdefault(key, value)

        return state
    
    def saveUserState(self) -> None:
        stateFile = self.repoRoot / LLAMA_CPP_USER_STATE_FILE
        saveJson5File(stateFile, self.userState)
    
    def initUI(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        
        turnixGroup = QGroupBox("Turnix")
        turnixGroup.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        turnixLayout = QVBoxLayout()

        turnixButtonRow = QHBoxLayout()
        turnixButtonRow.addWidget(self.makeButton("Start", self.startTurnix))
        turnixButtonRow.addWidget(self.makeButton("Restart", self.restartTurnix))
        turnixButtonRow.addWidget(self.makeButton("Stop", self.stopTurnix))
        turnixLayout.addLayout(turnixButtonRow)

        self.useLlamaCppProviderBox = QCheckBox("Use llama.cpp as provider")
        self.useLlamaCppProviderBox.setChecked(bool(self.userState.get("useLlamaCppProvider", True)))
        self.useLlamaCppProviderBox.stateChanged.connect(self.toggleLlamaCppProvider)
        turnixLayout.addWidget(self.useLlamaCppProviderBox)
        turnixGroup.setLayout(turnixLayout)
        layout.addWidget(turnixGroup)
        
        self.llamaCppGroup = QGroupBox("Llama.cpp")
        self.llamaCppGroup.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        llamaCppLayout = QVBoxLayout()
        self.llamaCppGroup.setLayout(llamaCppLayout)
        
        llamaCppButtonRow = QHBoxLayout()
        llamaCppButtonRow.addWidget(self.makeButton("Start", self.startLlamaCpp))
        llamaCppButtonRow.addWidget(self.makeButton("Restart", self.restartLlamaCpp))
        llamaCppButtonRow.addWidget(self.makeButton("Stop", self.stopLlamaCpp))
        llamaCppLayout.addLayout(llamaCppButtonRow)
        
        self.llamaCppSettingsButton = QToolButton()
        self.llamaCppSettingsButton.setText("Settings")
        self.llamaCppSettingsButton.setCheckable(True)
        self.llamaCppSettingsButton.setChecked(bool(self.userState.get("llamaCppSettingsExpanded", False)))
        self.llamaCppSettingsButton.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.llamaCppSettingsButton.clicked.connect(self.toggleLlamaCppSettings)
        llamaCppLayout.addWidget(self.llamaCppSettingsButton)
        
        self.llamaCppSettingsContent = QWidget()
        self.llamaCppSettingsContent.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        settingsLayout = QVBoxLayout()
        settingsLayout.setContentsMargins(0, 0, 0, 0)
        self.llamaCppSettingsContent.setLayout(settingsLayout)

        runtimeRow = QHBoxLayout()
        runtimeRow.addWidget(QLabel("Server"), 0)
        self.serverPathEdit = QLineEdit(str(self.userState.get("serverPath", "")))
        self.serverPathEdit.editingFinished.connect(self.saveLlamaCppConfigEdits)
        runtimeRow.addWidget(self.serverPathEdit, 1)
        self.refreshModelsButton = self.makeButton("Refresh models", self.refreshModels)
        runtimeRow.addWidget(self.refreshModelsButton, 0)
        settingsLayout.addLayout(runtimeRow)
        
        modelDirectoriesRow = QHBoxLayout()
        modelDirectoriesRow.addWidget(QLabel("Model dirs"), 0)
        self.modelDirectoriesEdit = QLineEdit(self.modelDirectoriesText())
        self.modelDirectoriesEdit.setToolTip(
            "Directories searched recursively for .gguf files. Separate multiple directories with semicolons."
        )
        self.modelDirectoriesEdit.editingFinished.connect(self.saveLlamaCppConfigEdits)
        modelDirectoriesRow.addWidget(self.modelDirectoriesEdit, 1)
        settingsLayout.addLayout(modelDirectoriesRow)
        
        modelRow = QHBoxLayout()
        modelRow.addWidget(QLabel("Model"), 0)
        self.modelBox = QComboBox()
        self.modelBox.currentIndexChanged.connect(self.selectCurrentModel)
        modelRow.addWidget(self.modelBox, 1)
        settingsLayout.addLayout(modelRow)
        
        profileRow = QHBoxLayout()
        profileRow.addWidget(QLabel("Profile"), 0)
        self.profileBox = QComboBox()
        self.profileBox.currentIndexChanged.connect(self.selectCurrentProfile)
        profileRow.addWidget(self.profileBox, 1)
        
        self.saveProfileButton = self.makeButton("Save", self.saveCurrentProfile)
        self.saveAsProfileButton = self.makeButton("Save As...", self.saveCurrentProfileAs)
        self.deleteProfileButton = self.makeButton("Del", self.deleteCurrentProfile)
        profileRow.addWidget(self.saveProfileButton)
        profileRow.addWidget(self.saveAsProfileButton)
        profileRow.addWidget(self.deleteProfileButton)
        settingsLayout.addLayout(profileRow)
        
        optionsGroup = QGroupBox("Server options")
        optionsGroup.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        optionsLayout = QGridLayout()
        optionsLayout.setContentsMargins(8, 8, 8, 8)
        optionsLayout.setHorizontalSpacing(12)
        optionsLayout.setVerticalSpacing(6)

        for column in range(3):
            optionsLayout.setColumnStretch(column, 1)

        self.hostEdit = self.makeLineEdit("host")
        self.portBox = self.makeSpinBox(1, 65535, "port")
        self.verbosityBox = self.makeSpinBox(0, 5, "verbosity")
        self.addOptionCell(optionsLayout, 0, 0, "Host", self.hostEdit, "host")
        self.addOptionCell(optionsLayout, 0, 1, "Port", self.portBox, "port")
        self.addOptionCell(optionsLayout, 0, 2, "Verbosity", self.verbosityBox, "verbosity")

        self.ctxSizeBox = self.makeSpinBox(0, 1_000_000, "ctxSize")
        self.gpuLayersEdit = self.makeLineEdit("gpuLayers")
        self.cpuMoeLayersBox = self.makeSpinBox(0, 128, "cpuMoeLayers")
        self.addOptionCell(optionsLayout, 1, 0, "Context size", self.ctxSizeBox, "ctxSize")
        self.addOptionCell(optionsLayout, 1, 1, "GPU layers", self.gpuLayersEdit, "gpuLayers")
        self.addOptionCell(optionsLayout, 1, 2, "CPU MoE layers", self.cpuMoeLayersBox, "cpuMoeLayers")

        self.flashAttentionBox = self.makeComboBox(["auto", "on", "off"], "flashAttention")
        self.cacheTypeKBox = self.makeComboBox(CACHE_TYPES, "cacheTypeK")
        self.cacheTypeVBox = self.makeComboBox(CACHE_TYPES, "cacheTypeV")
        self.addOptionCell(optionsLayout, 2, 0, "Flash Attention", self.flashAttentionBox, "flashAttention")
        self.addOptionCell(optionsLayout, 2, 1, "Cache K", self.cacheTypeKBox, "cacheTypeK")
        self.addOptionCell(optionsLayout, 2, 2, "Cache V", self.cacheTypeVBox, "cacheTypeV")

        self.kvOffloadBox = self.makeCheckBox("", "kvOffload")
        self.mlockBox = self.makeCheckBox("", "mlock")
        self.mmapBox = self.makeCheckBox("", "mmap")
        self.addOptionCell(optionsLayout, 3, 0, "KV offload", self.kvOffloadBox, "kvOffload")
        self.addOptionCell(optionsLayout, 3, 1, "Mlock", self.mlockBox, "mlock")
        self.addOptionCell(optionsLayout, 3, 2, "Mmap", self.mmapBox, "mmap")

        self.specialTokensBox = self.makeCheckBox("", "specialTokens")
        self.spmInfillBox = self.makeCheckBox("", "spmInfill")
        self.addOptionCell(optionsLayout, 4, 0, "Special tokens", self.specialTokensBox, "specialTokens")
        self.addOptionCell(optionsLayout, 4, 1, "SPM infill", self.spmInfillBox, "spmInfill")

        self.metricsBox = self.makeCheckBox("", "metrics")
        self.propsBox = self.makeCheckBox("", "props")
        self.slotsBox = self.makeCheckBox("", "slots")
        self.addOptionCell(optionsLayout, 5, 0, "Metrics", self.metricsBox, "metrics")
        self.addOptionCell(optionsLayout, 5, 1, "Props", self.propsBox, "props")
        self.addOptionCell(optionsLayout, 5, 2, "Slots", self.slotsBox, "slots")

        optionsGroup.setLayout(optionsLayout)
        settingsLayout.addWidget(optionsGroup)
        
        llamaCppLayout.addWidget(self.llamaCppSettingsContent)
        layout.addWidget(self.llamaCppGroup)
        
        self.setLayout(layout)
        self.updateLlamaCppProviderVisibility(saveState=False)
        self.updateLlamaCppSettingsVisibility(saveState=False)
        QTimer.singleShot(0, self.refitWindowHeight)

    def makeButton(self, text: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(callback)
        return button
    
    def makeLabel(self, text: str, tooltipKey: str) -> QLabel:
        label = QLabel(text)
        self.setWidgetTooltip(label, tooltipKey)
        return label
    
    def makeSpinBox(self, minimum: int, maximum: int, tooltipKey: str) -> QSpinBox:
        spinBox = QSpinBox()
        spinBox.setRange(minimum, maximum)
        spinBox.valueChanged.connect(self.noteSettingsChanged)
        self.setWidgetTooltip(spinBox, tooltipKey)
        return spinBox
    
    def makeComboBox(self, items: list[str], tooltipKey: str) -> QComboBox:
        comboBox = QComboBox()
        comboBox.addItems(items)
        comboBox.currentIndexChanged.connect(self.noteSettingsChanged)
        self.setWidgetTooltip(comboBox, tooltipKey)
        return comboBox
    
    def makeCheckBox(self, text: str, tooltipKey: str) -> QCheckBox:
        checkBox = QCheckBox(text)
        checkBox.stateChanged.connect(self.noteSettingsChanged)
        self.setWidgetTooltip(checkBox, tooltipKey)
        return checkBox

    def makeLineEdit(self, tooltipKey: str) -> QLineEdit:
        lineEdit = QLineEdit()
        lineEdit.textChanged.connect(self.noteSettingsChanged)
        self.setWidgetTooltip(lineEdit, tooltipKey)
        return lineEdit

    def setWidgetTooltip(self, widget: QWidget, tooltipKey: str) -> None:
        tooltip = LLAMA_CPP_OPTIONS_TOOLTIPS.get(tooltipKey)
        if tooltip:
            widget.setToolTip(tooltip)

    def addOptionCell(
        self,
        layout: QGridLayout,
        row: int,
        column: int,
        labelText: str,
        widget: QWidget,
        tooltipKey: str,
    ) -> None:
        cell = QWidget()
        cell.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        cellLayout = QHBoxLayout()
        cellLayout.setContentsMargins(0, 0, 0, 0)
        cellLayout.setSpacing(4)

        label = self.makeLabel(labelText, tooltipKey)
        cellLayout.addWidget(label, 0)
        cellLayout.addWidget(widget, 1)

        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if isinstance(widget, QCheckBox):
            widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        cell.setLayout(cellLayout)
        layout.addWidget(cell, row, column)

    def scheduleRefitWindowHeight(self) -> None:
        QTimer.singleShot(0, self.refitWindowHeight)

    def refitWindowHeight(self) -> None:
        layout = self.layout()
        if layout:
            layout.invalidate()
            layout.activate()

        self.setMinimumHeight(0)
        self.setMaximumHeight(16_777_215)
        self.adjustSize()

        targetHeight = self.sizeHint().height()
        self.setMinimumHeight(targetHeight)
        self.setMaximumHeight(targetHeight)
        self.resize(self.width(), targetHeight)
    
    def toggleLlamaCppProvider(self) -> None:
        if self.loadingUi:
            return
        
        checked = self.useLlamaCppProviderBox.isChecked()
        
        if not checked and self.isLlamaCppRunning():
            action = self.askWhatToDoWithRunningLlamaCpp()
            if action == "cancel":
                self.loadingUi = True
                try:
                    self.useLlamaCppProviderBox.setChecked(True)
                finally:
                    self.loadingUi = False
                self.scheduleRefitWindowHeight()
                return
            
            if action == "end":
                self.stopLlamaCpp()
            
            # "leave" intentionally keeps the tracked process alive.
        
        self.updateLlamaCppProviderVisibility(saveState=True)
    
    def updateLlamaCppProviderVisibility(self, *, saveState: bool) -> None:
        checked = self.useLlamaCppProviderBox.isChecked()
        self.llamaCppGroup.setVisible(checked)
        self.llamaCppGroup.updateGeometry()
        self.updateGeometry()
        
        if saveState:
            self.userState["useLlamaCppProvider"] = checked
            self.saveUserState()
            self.saveLlamaCppConfigEdits()
        
        self.scheduleRefitWindowHeight()
    
    def askWhatToDoWithRunningLlamaCpp(self) -> str:
        messageBox = QMessageBox(self)
        messageBox.setWindowTitle("Llama.cpp is running")
        messageBox.setText(
            "Llama.cpp instance is currently running.\n\n"
            "Do you want to end the process, leave it running, or cancel?"
        )
        
        endButton = messageBox.addButton("End process", QMessageBox.ButtonRole.DestructiveRole)
        leaveButton = messageBox.addButton("Leave running", QMessageBox.ButtonRole.AcceptRole)
        messageBox.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        messageBox.exec()
        
        clickedButton = messageBox.clickedButton()
        if clickedButton == endButton:
            return "end"
        if clickedButton == leaveButton:
            return "leave"
        return "cancel"
    
    def toggleLlamaCppSettings(self) -> None:
        if self.loadingUi:
            return
        
        self.updateLlamaCppSettingsVisibility(saveState=True)
    
    def updateLlamaCppSettingsVisibility(self, *, saveState: bool) -> None:
        expanded = self.llamaCppSettingsButton.isChecked()
        
        self.llamaCppSettingsContent.setVisible(expanded)
        self.llamaCppSettingsContent.updateGeometry()
        self.llamaCppGroup.updateGeometry()
        self.updateGeometry()

        if expanded:
            self.llamaCppSettingsButton.setArrowType(Qt.ArrowType.DownArrow)
        else:
            self.llamaCppSettingsButton.setArrowType(Qt.ArrowType.RightArrow)

        if saveState:
            self.userState["llamaCppSettingsExpanded"] = expanded
            self.saveUserState()
        
        self.scheduleRefitWindowHeight()
    
    def askWhetherToStartLlamaCppForTurnix(self) -> str:
        messageBox = QMessageBox(self)
        messageBox.setWindowTitle("Llama.cpp is not running")
        messageBox.setText(
            "Llama.cpp is selected as the Turnix provider, but no llama.cpp server "
            "is currently running.\n\n"
            "Do you want to start llama.cpp too?"
        )
        
        yesButton = messageBox.addButton("Yes", QMessageBox.ButtonRole.AcceptRole)
        noButton = messageBox.addButton("No", QMessageBox.ButtonRole.RejectRole)
        messageBox.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        messageBox.exec()
        
        clickedButton = messageBox.clickedButton()
        if clickedButton == yesButton:
            return "yes"
        if clickedButton == noButton:
            return "no"
        return "cancel"
    
    def prepareLlamaCppForTurnixStart(self) -> bool:
        if not self.useLlamaCppProviderBox.isChecked():
            return True
        
        if self.isLlamaCppRunning():
            return True
        
        action = self.askWhetherToStartLlamaCppForTurnix()
        if action == "cancel":
            return False
        
        if action == "no":
            return True
        
        self.startLlamaCpp()
        return self.isLlamaCppRunning()
    
    def modelDirectoriesText(self) -> str:
        modelDirectories = self.userState.get("modelDirectories", [])
        if not isinstance(modelDirectories, list):
            return ""
        
        return "; ".join(str(directory) for directory in modelDirectories)
    
    def modelDirectoriesFromText(self) -> list[str]:
        return [
            directory.strip()
            for directory in self.modelDirectoriesEdit.text().split(";")
            if directory.strip()

        ]

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

    def refreshModels(self) -> None:
        self.saveLlamaCppConfigEdits()
        self.llamaCppModels = self.discoverLlamaCppModels()
        
        currentKey = self.selectedModel.profileKey if self.selectedModel else str(self.userState.get("lastSelectedModelKey", ""))
        self.loadingUi = True
        try:
            self.modelBox.clear()
            
            for model in self.llamaCppModels:
                self.modelBox.addItem(model.displayName, model.profileKey)
            
            if currentKey:
                index = self.modelBox.findData(currentKey)
                if index >= 0:
                    self.modelBox.setCurrentIndex(index)
        
        finally:
            self.loadingUi = False

        if self.modelBox.count() > 0:
            self.selectCurrentModel()

    def discoverLlamaCppModels(self) -> list[LlamaCppModel]:
        modelDirectories = self.userState.get("modelDirectories", [])
        if not isinstance(modelDirectories, list):
            print("llama.cpp modelDirectories must be a list.")
            return []
        
        discovered: list[tuple[Path, Path, Path]] = []
        
        for directory in modelDirectories:
            modelRoot = Path(str(directory))
            if not modelRoot.exists():
                print(f"Model directory not found: {modelRoot}")
                continue
            
            for modelPath in modelRoot.rglob("*.gguf"):
                try:
                    relativePath = modelPath.relative_to(modelRoot)
                except ValueError:
                    relativePath = Path(modelPath.name)
                
                discovered.append((modelRoot, modelPath, relativePath))
        
        fileNameCounts: dict[str, int] = {}
        profileKeyCounts: dict[str, int] = {}
        for _modelRoot, modelPath, relativePath in discovered:
            fileNameCounts[modelPath.name] = fileNameCounts.get(modelPath.name, 0) + 1
            profileKey = relativePath.as_posix()
            profileKeyCounts[profileKey] = profileKeyCounts.get(profileKey, 0) + 1
        
        models: list[LlamaCppModel] = []
        for modelRoot, modelPath, relativePath in discovered:
            profileKey = relativePath.as_posix()
            if profileKeyCounts[profileKey] > 1:
                profileKey = f"{modelRoot.as_posix()}::{relativePath.as_posix()}"
            
            if fileNameCounts[modelPath.name] == 1:
                displayName = modelPath.name
            else:
                displayName = relativePath.as_posix()
            
            models.append(
                LlamaCppModel(
                    displayName=displayName,
                    modelPath=modelPath,
                    modelRoot=modelRoot,
                    relativePath=relativePath,
                    profileKey=profileKey,
                )
            )
        
        return sorted(models, key=lambda model: model.displayName.lower())
    
    def selectCurrentModel(self) -> None:
        if self.loadingUi:
            return
        
        profileKey = self.modelBox.currentData()
        self.selectedModel = None
        
        for model in self.llamaCppModels:
            if model.profileKey == profileKey:
                self.selectedModel = model
                break
        
        self.rememberSelectedModel()
        self.reloadProfileBoxForSelectedModel()
        self.applyResolvedProfileForSelectedModel()

    def rememberSelectedModel(self) -> None:
        if not self.selectedModel:
            return

        if self.userState.get("lastSelectedModelKey") == self.selectedModel.profileKey:
            return

        self.userState["lastSelectedModelKey"] = self.selectedModel.profileKey
        self.saveUserState()

    def reloadProfileBoxForSelectedModel(self) -> None:
        self.loadingUi = True
        try:
            self.profileBox.clear()
            self.profileBox.addItem("Default", {"kind": "default"})
            self.profileBox.addItem("Custom", {"kind": "custom"})
            
            for profileName in sorted(self.getNamedProfilesForSelectedModel().keys()):
                self.profileBox.addItem(profileName, {"kind": "named", "name": profileName})

        finally:
            self.loadingUi = False
    
    def applyResolvedProfileForSelectedModel(self) -> None:
        selection = self.resolveProfileSelectionForSelectedModel()
        self.applyProfileSelection(selection)
    
    def resolveProfileSelectionForSelectedModel(self) -> dict[str, str]:
        namedProfiles = self.getNamedProfilesForSelectedModel()
        remembered = self.getRememberedProfileForSelectedModel()
        
        if namedProfiles:
            rememberedSelection = remembered.get("selectedProfile") if isinstance(remembered, dict) else None
            if isinstance(rememberedSelection, dict):
                rememberedName = rememberedSelection.get("name")
                if rememberedSelection.get("kind") == "named" and rememberedName in namedProfiles:
                    return {"kind": "named", "name": str(rememberedName)}
            
            firstProfileName = sorted(namedProfiles.keys())[0]
            return {"kind": "named", "name": firstProfileName}
        
        if isinstance(remembered, dict) and isinstance(remembered.get("lastUsedSettings"), dict):
            return {"kind": "custom"}
        
        return {"kind": "default"}

    def applyProfileSelection(self, selection: dict[str, str]) -> None:
        kind = selection.get("kind", "default")
        name = selection.get("name")
        settings = self.getSettingsForProfileSelection(kind=kind, name=name)
        
        self.loadingUi = True
        try:
            self.setSettingsToUi(settings)
            self.resetProfileDisplayNames()
            self.selectedProfileKind = kind
            self.selectedProfileName = name if kind == "named" else None
            self.profileIsModified = False
            self.selectProfileBoxItem(kind=kind, name=name)
            self.refreshProfileButtons()
        finally:
            self.loadingUi = False
    
    def selectProfileBoxItem(self, *, kind: str, name: str | None) -> None:
        for index in range(self.profileBox.count()):
            data = self.profileBox.itemData(index)
            if not isinstance(data, dict):
                continue
            
            if data.get("kind") != kind:
                continue
            
            if kind == "named" and data.get("name") != name:
                continue
            
            self.profileBox.setCurrentIndex(index)
            return
    
    def resetProfileDisplayNames(self) -> None:
        for index in range(self.profileBox.count()):
            data = self.profileBox.itemData(index)
            if isinstance(data, dict) and data.get("kind") == "named":
                self.profileBox.setItemText(index, str(data.get("name", "")))
    
    def selectCurrentProfile(self) -> None:
        if self.loadingUi:
            return
        
        data = self.profileBox.currentData()
        if not isinstance(data, dict):
            return
        
        self.applyProfileSelection(data)
    
    def noteSettingsChanged(self) -> None:
        if self.loadingUi:
            return
        
        if self.selectedProfileKind == "named":
            self.profileIsModified = True
            self.updateNamedProfileDisplay()
        elif self.selectedProfileKind == "default":
            self.selectedProfileKind = "custom"
            self.selectedProfileName = None
            self.profileIsModified = False

            self.loadingUi = True
            try:
                self.selectProfileBoxItem(kind="custom", name=None)
            finally:
                self.loadingUi = False
        
        self.refreshProfileButtons()
    
    def updateNamedProfileDisplay(self) -> None:
        if self.selectedProfileKind != "named" or not self.selectedProfileName:
            return
        
        for index in range(self.profileBox.count()):
            data = self.profileBox.itemData(index)
            if(
                isinstance(data, dict)
                and data.get("kind") == "named"
                and data.get("name") == self.selectedProfileName
            ):
                displayName = self.selectedProfileName
                if self.profileIsModified:
                    displayName = f"{displayName} (*)"
                self.profileBox.setItemText(index, displayName)
                return
    
    def refreshProfileButtons(self) -> None:
        isNamed = self.selectedProfileKind == "named"
        self.saveProfileButton.setEnabled(isNamed and self.profileIsModified)
        self.saveAsProfileButton.setEnabled(self.selectedProfileKind in {"custom", "named"})
        self.deleteProfileButton.setEnabled(isNamed)
    
    def getSettingsForProfileSelection(self, *, kind: str, name: str | None) -> dict[str, Any]:
        settings = self.getDefaultLlamaCppOptions()

        if kind == "named" and name:
            settings.update(self.getNamedProfilesForSelectedModel().get(name, {}))
            return normalizedSettingsMap(settings)

        if kind == "custom":
            remembered = self.getRememberedProfileForSelectedModel()
            if isinstance(remembered, dict) and isinstance(remembered.get("lastUsedSettings"), dict):
                settings.update(remembered["lastUsedSettings"])
            return normalizedSettingsMap(settings)
        
        return normalizedSettingsMap(settings)
    
    def getDefaultLlamaCppOptions(self) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        defaultOptions = self.llamaCppConfig.get("defaultOptions", {})
        if isinstance(defaultOptions, dict):
            settings.update(defaultOptions)

        localDefaultOptions = self.userState.get("localDefaultOptions", {})
        if isinstance(localDefaultOptions, dict):
            settings.update(localDefaultOptions)

        return normalizedSettingsMap(settings)
    
    def getNamedProfilesForSelectedModel(self) -> dict[str, dict[str, Any]]:
        if not self.selectedModel:
            return {}
        
        profilesByModel = self.userState.setdefault("namedProfilesByModel", {})
        if not isinstance(profilesByModel, dict):
            self.userState["namedProfilesByModel"] = {}
            profilesByModel = self.userState["namedProfilesByModel"]
        
        modelProfiles = profilesByModel.setdefault(self.selectedModel.profileKey, {})
        if isinstance(modelProfiles, dict):
            return modelProfiles
        
        profilesByModel[self.selectedModel.profileKey] = {}
        return profilesByModel[self.selectedModel.profileKey]
    
    def getRememberedProfileForSelectedModel(self) -> dict[str, Any]:
        if not self.selectedModel:
            return {}
        
        rememberedProfiles = self.userState.setdefault("rememberedModelProfiles", {})
        if not isinstance(rememberedProfiles, dict):
            self.userState["rememberedModelProfiles"] = {}
            rememberedProfiles = self.userState["rememberedModelProfiles"]

        remembered = rememberedProfiles.setdefault(self.selectedModel.profileKey, {})
        if isinstance(remembered, dict):
            return remembered
        
        rememberedProfiles[self.selectedModel.profileKey] = {}
        return rememberedProfiles[self.selectedModel.profileKey]
    
    def currentSettingsFromUi(self) -> dict[str, Any]:
        return {
            "ctxSize": self.ctxSizeBox.value(),
            "flashAttention": self.flashAttentionBox.currentText(),
            "kvOffload": self.kvOffloadBox.isChecked(),
            "cacheTypeK": self.cacheTypeKBox.currentText(),
            "cacheTypeV": self.cacheTypeVBox.currentText(),
            "mlock": self.mlockBox.isChecked(),
            "mmap": self.mmapBox.isChecked(),
            "cpuMoeLayers": self.cpuMoeLayersBox.value(),
            "gpuLayers": self.gpuLayersEdit.text().strip() or "auto",
            "verbosity": self.verbosityBox.value(),
            "specialTokens": self.specialTokensBox.isChecked(),
            "spmInfill": self.spmInfillBox.isChecked(),
            "host": self.hostEdit.text().strip() or "127.0.0.1",
            "port": self.portBox.value(),
            "metrics": self.metricsBox.isChecked(),
            "props": self.propsBox.isChecked(),
            "slots": self.slotsBox.isChecked(),
        }
    
    def setSettingsToUi(self, settings: dict[str, Any]) -> None:
        normalized = normalizedSettingsMap(settings)
        
        self.ctxSizeBox.setValue(int(normalized["ctxSize"]))
        self.flashAttentionBox.setCurrentText(str(normalized["flashAttention"]))
        self.kvOffloadBox.setChecked(bool(normalized["kvOffload"]))
        self.cacheTypeKBox.setCurrentText(str(normalized["cacheTypeK"]))
        self.cacheTypeVBox.setCurrentText(str(normalized["cacheTypeV"]))
        self.mlockBox.setChecked(bool(normalized["mlock"]))
        self.mmapBox.setChecked(bool(normalized["mmap"]))
        self.cpuMoeLayersBox.setValue(int(normalized["cpuMoeLayers"]))
        self.gpuLayersEdit.setText(str(normalized["gpuLayers"]))
        self.verbosityBox.setValue(int(normalized["verbosity"]))
        self.specialTokensBox.setChecked(bool(normalized["specialTokens"]))
        self.spmInfillBox.setChecked(bool(normalized["spmInfill"]))
        self.hostEdit.setText(str(normalized["host"]))
        self.portBox.setValue(int(normalized["port"]))
        self.metricsBox.setChecked(bool(normalized["metrics"]))
        self.propsBox.setChecked(bool(normalized["props"]))
        self.slotsBox.setChecked(bool(normalized["slots"]))
    
    def saveLlamaCppConfigEdits(self) -> None:
        self.userState["serverPath"] = self.serverPathEdit.text().strip()
        self.userState["modelDirectories"] = self.modelDirectoriesFromText()
        self.saveUserState()
        self.saveLlamaCppConfig()
    
    def saveCurrentProfile(self) -> None:
        if self.selectedProfileKind != "named" or not self.selectedProfileName:
            return
        
        namedProfiles = self.getNamedProfilesForSelectedModel()
        namedProfiles[self.selectedProfileName] = self.currentSettingsFromUi()
        self.saveUserState()
        
        self.profileIsModified = False
        self.updateNamedProfileDisplay()
        self.refreshProfileButtons()
        self.rememberCurrentProfileSelection()
    
    def saveCurrentProfileAs(self) -> None:
        if not self.selectedModel:
            return
        
        if self.selectedProfileKind == "named" and self.selectedProfileName:
            defaultName = self.selectedProfileName
        else:
            defaultName = self.selectedModel.displayName
        
        dialog = ProfileNameDialog(
            title="Save Profile As",
            defaultName=defaultName,
            parent=self,
        )
        
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        profileName = dialog.profileName()
        if not profileName:
            return
        
        namedProfiles = self.getNamedProfilesForSelectedModel()
        if profileName in namedProfiles:
            response = QMessageBox.question(
                self,
                "Replace Profile",
                f'Profile "{profileName}" already exists for this model. Replace it?',
            )
            if response != QMessageBox.StandardButton.Yes:
                return
        
        namedProfiles[profileName] = self.currentSettingsFromUi()
        self.saveUserState()
        
        self.reloadProfileBoxForSelectedModel()
        self.applyProfileSelection({"kind": "named", "name": profileName})
        self.rememberCurrentProfileSelection()
    
    def deleteCurrentProfile(self) -> None:
        if self.selectedProfileKind != "named" or not self.selectedProfileName:
            return
        
        message = f'Delete profile "{self.selectedProfileName}" for this model?'
        if self.profileIsModified:
            message += "\n\nThis profile has unsaved changes. Delete the saved profile anyway?"
        
        response = QMessageBox.question(self, "Delete Profile", message)
        if response != QMessageBox.StandardButton.Yes:
            return
        
        namedProfiles = self.getNamedProfilesForSelectedModel()
        namedProfiles.pop(self.selectedProfileName, None)
        self.saveUserState()
        
        self.rememberSelectedModel()
        self.reloadProfileBoxForSelectedModel()
        self.applyResolvedProfileForSelectedModel()
    
    def rememberCurrentProfileSelection(self) -> None:
        if not self.selectedModel:
            return
        
        remembered = self.getRememberedProfileForSelectedModel()
        remembered["selectedProfile"] = self.currentProfileSelectionForStorage()
        remembered["lastUsedSettings"] = self.currentSettingsFromUi()
        self.saveUserState()
    
    def currentProfileSelectionForStorage(self) -> dict[str, str]:
        if self.selectedProfileKind == "named" and self.selectedProfileName:
            return {"kind": "named", "name": self.selectedProfileName}
        if self.selectedProfileKind == "custom":
            return {"kind": "custom"}
        return {"kind": "default"}
    
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
            
            if self.useLlamaCppProviderBox.isChecked():
                if not self.prepareLlamaCppForTurnixStart():
                    return
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

    def isLlamaCppRunning(self) -> bool:
        return self.llamaCppProcess is not None and self.llamaCppProcess.poll() is None
    
    def startLlamaCpp(self) -> None:
        try:
            if self.isLlamaCppRunning():
                print("Llama.cpp already running.")
                return
            
            if self.llamaCppProcess and self.llamaCppProcess.poll() is not None:
                self.llamaCppProcess = None
            
            if not self.selectedModel:
                print("No llama.cpp model selected.")
                return
            
            exe = self.serverPathEdit.text().strip()
            if not exe:
                print("No llama.cpp server path configured.")
                return
            
            exePath = Path(exe)
            if not exePath.exists():
                print(f"No llama.cpp server executable found at path: {exePath}")
                return
            
            settings = self.currentSettingsFromUi()
            args = self.buildLlamaCppArgs(modelPath=self.selectedModel.modelPath, settings=settings)
            
            argsQuoted = " ".join(self.quotePowerShellArg(arg) for arg in args)
            exeQuoted = self.quotePowerShellArg(exe)
            cmdStr = f'$host.UI.RawUI.WindowTitle = "LlamaCPP"; & {exeQuoted} {argsQuoted}'
            pwshCmd = ["pwsh", "-NoExit", "-Command", cmdStr]

            print("Starting Llama.cpp server in visible console with persistent shell...")
            print("Command:", " ".join(pwshCmd))
            self.llamaCppProcess = psutil.Popen(
                pwshCmd,
                cwd=self.repoRoot,
                creationflags=CREATE_NEW_CONSOLE,
            )
            print(f"Llama.cpp PID: {self.llamaCppProcess.pid}")
            
            self.userState["serverPath"] = exe
            self.saveUserState()
            self.saveLlamaCppConfig()
            self.rememberCurrentProfileSelection()
            
        except Exception as err:
            print(f"Error starting Llama.cpp: {err}")

    def restartLlamaCpp(self) -> None:
        print("Restarting Llama.cpp...")
        self.stopLlamaCpp()
        self.startLlamaCpp()

    def stopLlamaCpp(self) -> None:
        try:
            if self.llamaCppProcess:
                if self.isLlamaCppRunning():
                    print(f"Stopping Llama.cpp PID {self.llamaCppProcess.pid} and its children...")
                    self.killProcessTree(self.llamaCppProcess)
                self.llamaCppProcess = None
        except Exception as err:
            print(f"Error stopping Llama.cpp: {err}")

    def buildLlamaCppArgs(self, *, modelPath: Path, settings: dict[str, Any]) -> list[str]:
        args = [
            "--model",
            str(modelPath),
            "--no-ui",
            "--ctx-size",
            str(settings["ctxSize"]),
            "--flash-attn",
            str(settings["flashAttention"]),
            "--cache-type-k",
            str(settings["cacheTypeK"]),
            "--cache-type-v",
            str(settings["cacheTypeV"]),
            "--n-cpu-moe",
            str(settings["cpuMoeLayers"]),
            "--n-gpu-layers",
            str(settings["gpuLayers"]),
            "--verbosity",
            str(settings["verbosity"]),
            "--host",
            str(settings["host"]),
            "--port",
            str(settings["port"]),
        ]
        
        if settings["kvOffload"]:
            args.append("--kv-offload")
        else:
            args.append("--no-kv-offload")

        if settings["mlock"]:
            args.append("--mlock")

        if settings["mmap"]:
            args.append("--mmap")
        else:
            args.append("--no-mmap")
        
        if settings["specialTokens"]:
            args.append("--special")
        
        if settings["spmInfill"]:
            args.append("--spm-infill")
        
        if settings["metrics"]:
            args.append("--metrics")

        if settings["props"]:
            args.append("--props")
        
        if settings["slots"]:
            args.append("--slots")
        else:
            args.append("--no-slots")

        return args

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
            self.saveLlamaCppConfigEdits()
        except Exception as err:
            print(f"Error saving llama.cpp config on exit: {err}")
        
        try:
            self.stopTurnix()
        except Exception as err:
            print(f"Error stopping Turnix on exit: {err}")

        try:
            self.stopLlamaCpp()
        except Exception as err:
            print(f"Error stopping Llama.cpp on exit: {err}")

        event.accept()


if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        window = Launcher()
        window.show()
        sys.exit(app.exec())
    except Exception as err:
        print(f"Error starting the application: {err}")
