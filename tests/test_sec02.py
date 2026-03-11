"""
SEC-02 automated verification tests.

These tests verify that sensitive files are listed in .gitignore.

Both tests will FAIL until Plan 02 creates the .gitignore file with the
required entries. That is correct and expected.
"""

import pathlib


def test_env_in_gitignore():
    """Assert .env appears as a line entry in .gitignore."""
    project_root = pathlib.Path(__file__).parent.parent
    gitignore_path = project_root / ".gitignore"

    assert gitignore_path.exists(), (
        ".gitignore does not exist. Create it with .env and chat_memory.json entries."
    )

    content = gitignore_path.read_text()
    assert any(line.strip() == ".env" for line in content.splitlines()), (
        "'.env' not found as a standalone line entry in .gitignore. "
        "Add '.env' on its own line to prevent committing credentials."
    )


def test_chat_memory_in_gitignore():
    """Assert chat_memory.json appears as a line entry in .gitignore."""
    project_root = pathlib.Path(__file__).parent.parent
    gitignore_path = project_root / ".gitignore"

    assert gitignore_path.exists(), (
        ".gitignore does not exist. Create it with .env and chat_memory.json entries."
    )

    content = gitignore_path.read_text()
    assert any(line.strip() == "chat_memory.json" for line in content.splitlines()), (
        "'chat_memory.json' not found as a standalone line entry in .gitignore. "
        "Add 'chat_memory.json' on its own line to prevent committing chat history."
    )
