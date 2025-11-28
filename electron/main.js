// electron/main.js
const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const path = require('path');

globalThis.__turnixDevMode = true;

function createMainWindow() {
    const win = new BrowserWindow({
        width: 1200,
        height: 800,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        },
    });

    win.loadURL('http://localhost:63726/?viewKind=main');

    if(globalThis.__turnixDevMode) {
        win.webContents.openDevTools();
    }

    return win;
}

function createTurnixDevToolsWindow() {
    const win = new BrowserWindow({
        width: 1200,
        height: 800,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        },
    });

    win.loadURL('http://localhost:63726/?viewKind=turnixdevtools');

    return win;
}

app.whenReady().then(() => {
    createMainWindow();

    const { globalShortcut } = require('electron');
    globalShortcut.register('CommandOrControl+Shift+D', () => {
        createTurnixDevToolsWindow();
    });

    return;
}).catch((error) => {
    console.error('Fatal startup error in main process:', error);

    dialog.showErrorBox(
        'Startup Error',
        `Electron failed to start:\n${error instanceof Error ? error.stack : String(error)}`
    );

    app.exit(1);
});

// IPC from renderer to open turnix devtools window
ipcMain.on('turnix-open-devtools', () => {
    createTurnixDevToolsWindow();
});

app.on('window-all-closed', () => {
    if(process.platform !== 'darwin') app.quit();
});
