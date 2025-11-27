// electron/preload.js
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('turnixElectron', {
    openTurnixDevTools() {
        ipcRenderer.send('turnix-open-devtools');
    },
});
