"""Manually exercise the voice receptionist without a live Vapi/Twilio call.

Feeds typed customer lines through the same handle_voice_turn() path the real
/voice/chat/completions endpoint uses, building up Vapi-shaped message history
exactly like Vapi would resend it each turn.
"""
import sys

from sqlmodel import Session

from db import engine, init_db
from db_models import Client
from service import agent
from voice_adapter import VapiCall, VapiChatRequest, VapiCustomer, VapiMessage, VapiPhoneNumber, handle_voice_turn


def main():
    if len(sys.argv) < 2:
        print("Usage: python simulate_voice.py <client_id>")
        sys.exit(1)

    client_id = int(sys.argv[1])
    init_db()

    with Session(engine) as session:
        client = session.get(Client, client_id)
        if client is None:
            print(f"No client with id {client_id}")
            sys.exit(1)

        print(f"Simulating a live voice call to {client.business_name} ({client.answer_mode}).")
        print("Type as the caller. Ctrl+C to quit.\n")

        vapi_messages = []
        while True:
            try:
                caller_line = input("Caller: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nCall ended.")
                break

            if not caller_line:
                continue

            vapi_messages.append(VapiMessage(role="user", content=caller_line))
            request = VapiChatRequest(
                model="claude-sonnet-4-6",
                messages=vapi_messages,
                call=VapiCall(
                    id="sim-call",
                    phoneNumber=VapiPhoneNumber(number=client.inbound_number or "+10000000000"),
                    customer=VapiCustomer(number="+15550001234"),
                ),
            )

            result = handle_voice_turn(session, agent, client, request)
            vapi_messages.append(VapiMessage(role="assistant", content=result["reply"]))

            print(f"Roster: {result['reply']}")
            if result["pending_tool_call"]:
                print(f"  [tool call] {result['pending_tool_call']}")
                break  # a transfer ends the simulated call


if __name__ == "__main__":
    main()
