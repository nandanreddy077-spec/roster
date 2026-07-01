import json
import sys
from pathlib import Path

from engine import AgentEngine
from models import ClientConfig


def load_client(path: str) -> ClientConfig:
    data = json.loads(Path(path).read_text())
    return ClientConfig(**data)


def main():
    if len(sys.argv) < 2:
        print("Usage: python simulate.py <client_config.json>")
        sys.exit(1)

    client = load_client(sys.argv[1])
    engine = AgentEngine()

    history = []

    print(f"Simulating a missed-call text-back for {client.business_name}.")
    print("Type as the customer. Ctrl+C to quit.\n")

    while True:
        try:
            user_msg = input("Customer: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nEnded.")
            break

        if not user_msg:
            continue

        history.append({"role": "user", "content": [{"type": "text", "text": user_msg}]})

        result = engine.respond(client, history)
        history.extend(result["new_messages"])  # engine threads tool turns itself

        print(f"Roster: {result['reply']}")
        for call in result["jobs"]:
            print(f"  [logged job] {json.dumps(call['input'])}")


if __name__ == "__main__":
    main()
