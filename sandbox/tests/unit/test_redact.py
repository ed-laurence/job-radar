from llm_proxy.redact import MARK, redact, redact_bytes


def test_known_secrets_are_removed() -> None:
    assert redact("key=abc123secret!", ["abc123secret"]) == f"key={MARK}!"


def test_anthropic_key_shape_and_bearer_values_are_removed() -> None:
    out = redact("x sk-ant-abcdefgh1234 Authorization: Bearer abcdefghijk", [])
    assert "sk-ant-abcdefgh1234" not in out
    assert "abcdefghijk" not in out


def test_empty_secret_is_ignored_and_bytes_work() -> None:
    assert redact_bytes(b"hello", ["", "zzz"]) == b"hello"
