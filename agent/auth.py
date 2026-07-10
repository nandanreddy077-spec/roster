"""Password hashing for the self-serve customer portal (agent/portal.py).
Fully separate from the founder's HTTP-Basic admin auth in app.py — no
shared credential path between the two.
"""
import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
