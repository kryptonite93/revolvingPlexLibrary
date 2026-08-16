from app.security.redaction import REDACTED, redact


def test_redacts_nested_secrets_and_bearer_tokens() -> None:
    result = redact(
        {
            "headers": {"Authorization": "Bearer abc.def.ghi"},
            "items": [{"password": "hunter2"}],
            "message": "token=abc123 safe=true",
        }
    )
    assert result["headers"]["Authorization"] == REDACTED
    assert result["items"][0]["password"] == REDACTED
    assert "abc123" not in result["message"]


def test_redacts_url_credentials_and_passkeys() -> None:
    value = "https://alice:password@example.test/announce?passkey=abc123&mode=compact"
    result = redact(value)
    assert "alice" not in result
    assert "password" not in result
    assert "abc123" not in result
    assert "mode=compact" in result
