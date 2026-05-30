# backend/adapters/terminalAdapter.py
from __future__ import annotations

from backend.pipeline.chatPipeline import EmptyUserMessageError
from backend.runtime.appInstance import AppInstance


class TerminalAdapter:
    """In-process terminal adapter for the default Turnix AppInstance."""

    def __init__(self, *, appInstance: AppInstance) -> None:
        self.appInstance = appInstance

    def run(self) -> None:
        print("Type /exit to quit.")
        print()

        while True:
            try:
                userText = input("you> ")
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                break

            if userText.strip() == "/exit" or userText.strip() == "/quit":
                break

            try:
                result = self.appInstance.handleUserMessage(userText)
            except EmptyUserMessageError as err:
                print(f"error> {err}")
                continue
            except Exception as err:
                print(f"error> {type(err).__name__}: {err}")
                continue

            print(f"ai> {result.modelResponse.content}")
            for infoMessage in result.infoMessages:
                print(f"info> {infoMessage}")
