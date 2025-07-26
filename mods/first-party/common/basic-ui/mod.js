export const modId = "basic-ui"

const cssStyle = `
body {
    margin: 0;
    padding: 0;
    height: 100vh;
    display: flex;
    flex-direction: column;
    font-family: sans-serif;
}

#prompt-area-container {
    padding: 10px;
    border-bottom: 1px solid #ccc;
    background: #eef;
}

#prompt-area {
    width: 100%;
    height: 60px;
    resize: none;
    padding: 8px;
    font-size: 14px;
    box-sizing: border-box;
}

#messages-container {
    flex: 1;
    padding: 10px;
    overflow-y: auto;
    background: #f5f5f5;
    display: flex;
    flex-direction: column;
}

.message {
    background: #e0e0e0;
    padding: 8px 12px;
    margin-bottom: 6px;
    border-radius: 6px;
    max-width: 100%;
    word-wrap: break-word;
    white-space: pre-line;
}

#message-area-container {
    display: flex;
    padding: 10px;
    border-top: 1px solid #cccccc;
    background: #ffffff;
}

#message-area {
    flex: 1;
    height: 60px;
    resize: none;
    padding: 8px;
    font-size: 14px;
}

#send-button {
    margin-left: 10px;
    padding: 0 20px;
    font-size: 14px;
    cursor: pointer;
}
`;

export class BasicUI {
    constructor() {
        this.elMessagesContainer = null;
        this.elCurrentStreamMessage = null;
        this.elPromptArea = null;
        this.elMessageArea = null;
        this.myCtx = null;
    }

    onSessionCreated(ctx, session) {
        console.log("[basic-ui:onSessionCreated] onSessionCreated", ctx, session);

        if(session?.sessionId !== "main") return;

        this.mainSession = session;

        console.debug("[basic-ui:onSessionCreated] Registering ProcessStreamResponse hook.");
        session.registerHook({
            modId: modId,
            stageName: "ProcessStreamResponse",
            handler: async (ctx, stageData) => {
                console.debug("[basic-ui:onSessionCreated(ProcessStreamResponse)] stageData", stageData);
                if(!this.elCurrentStreamMessage) {
                    this.elCurrentStreamMessage = document.createElement("div");
                    this.elCurrentStreamMessage.className = "message";
                    this.elMessagesContainer.appendChild(this.elCurrentStreamMessage);
                }
                //this.elCurrentStreamMessage.textContent += chunk;
                this.elMessagesContainer.scrollTop = this.elMessagesContainer.scrollHeight;
            }
        });

        console.debug("[basic-ui:onSessionCreated] Registering ReceivedResponse hook.");
        session.registerHook({
            modId: modId,
            stageName: "ReceivedResponse",
            handler: async (ctx, stageData) => {
                console.debug("[basic-ui:onSessionCreated(ReceivedResponse)] stageData", stageData);
                if(this.elCurrentStreamMessage && this.elCurrentStreamMessage.textContent === "") {
                    // Response wasn't streamed probably.
                    this.elCurrentStreamMessage.textContent = stageData.assistantMessage;
                }
                this.elCurrentStreamMessage = null;
            }
        });
    }

    async onActivate(ctx) {
        console.debug("[basic-ui:onActivate] Activating mod", ctx);
        this.myCtx = ctx;
    
        // Inject CSS
        const elStyle = document.createElement("style");
        elStyle.textContent = cssStyle;
        document.head.appendChild(elStyle);
        
        // Prompt area
        const elPromptAreaContainer = document.createElement("div");
        elPromptAreaContainer.id = "prompt-area-container";
    
        this.elPromptArea = document.createElement("textarea");
        this.elPromptArea.id = "prompt-area";
        elPromptAreaContainer.appendChild(this.elPromptArea);
    
        // Messages
        this.elMessagesContainer = document.createElement("div");
        this.elMessagesContainer.id = "messages-container";
        
        // InputArea
        const elMessageAreaContainer = document.createElement("div");
        elMessageAreaContainer.id = "message-area-container";
    
        this.elMessageArea = document.createElement("textarea");
        this.elMessageArea.id = "message-area";
        
        const elSendButton = document.createElement("button");
        elSendButton.id = "send-button";
        elSendButton.textContent = "Send";
    
        elSendButton.onclick = this.sendUserMessage.bind(this);
        this.elMessageArea.onkeydown = async (e) => {
            if(e.key === "Enter" && !e.shiftKey) {
                e.preventDefault(); // Prevents adding newline
                await this.sendUserMessage();
            }
        }

        elMessageAreaContainer.appendChild(this.elMessageArea);
        elMessageAreaContainer.appendChild(elSendButton);
    
        document.body.appendChild(elPromptAreaContainer);
        document.body.appendChild(this.elMessagesContainer);
        document.body.appendChild(elMessageAreaContainer);
    }
    
    async sendUserMessage() {
        // TODO: Add user message to messages list if message sent
        if(!this.mainSession) {
            // TODO: Uncomment when logging is fixed
            //this.myCtx.logger.warn("Main session not initialized, cannot send user message.");
            return;
        }

        let text = this.elMessageArea.value.trim();
        if(text === "") {
            // TODO: Uncomment when logging is fixed
            //this.myCtx.logger.info("Trying to send empty user message, ignoring...");
            return;
        };
        this.elMessageArea.value = "";

        const promptText = this.elPromptArea.value.trim();
        text = promptText + "\n\n" + text;

        await this.myCtx.sendUserMessage(text);
    }
}
