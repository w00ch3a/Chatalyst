from __future__ import annotations

from chatalyst.core.privacy import redact_project_reference, redact_project_refs


def test_redact_project_reference_hides_project_urls_and_ids():
    assert (
        redact_project_reference("https://chatgpt.com/g/private-project-id")
        == "https://chatgpt.com/g/[redacted]"
    )
    assert redact_project_reference("g-private-project-id") == "[redacted-project-id]"


def test_redact_project_refs_walks_nested_payloads():
    payload = {
        "url": "https://chatgpt.com/g/private-project-id",
        "items": [{"id": "g-private-project-id"}],
    }

    assert redact_project_refs(payload) == {
        "url": "https://chatgpt.com/g/[redacted]",
        "items": [{"id": "[redacted-project-id]"}],
    }
