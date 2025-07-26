const { app, BrowserWindow } = require("electron");
const path = require("path");

globalThis.__turnixDevMode = true;

function createWindow() {
    const win = new BrowserWindow({
        width: 1200,
        height: 800,
        webPreferences: {
            preload: path.join(__dirname, "preload.js"),
            contextIsolation: true,
            nodeIntegration: false,
        },
    });

    win.loadURL("http://localhost:3000/")

    if(globalThis.__turnixDevMode) {
        win.webContents.openDevTools()
    }
}

app.whenReady().then(() => {
    createWindow();
});
