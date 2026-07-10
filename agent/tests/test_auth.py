from auth import hash_password, verify_password


def test_hash_password_produces_a_verifiable_hash():
    hashed = hash_password("correct-horse-battery-staple")
    assert hashed != "correct-horse-battery-staple"
    assert verify_password("correct-horse-battery-staple", hashed)


def test_verify_password_rejects_wrong_password():
    hashed = hash_password("correct-horse-battery-staple")
    assert not verify_password("wrong-password", hashed)


def test_hash_password_is_salted():
    """Two hashes of the same password must differ (bcrypt salts per-call) —
    guards against someone swapping in a naive unsalted hash later."""
    assert hash_password("same-password") != hash_password("same-password")
