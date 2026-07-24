from backend.agent.command_classifier import classify_command
from backend.agent.decisive import is_timid_read_round, is_timid_shell_command


def test_cat_pipe_grep_read():
    assert classify_command("cat foo | grep bar") == "read"


def test_redirect_write():
    assert classify_command("cat foo > bar") == "write"
    assert classify_command("cat > out <<EOF\nx\nEOF") == "write"


def test_pytest_read():
    assert classify_command("python -m pytest -q") == "read"
    assert classify_command("pytest tests/") == "read"


def test_git():
    assert classify_command("git status") == "read"
    assert classify_command("git add . && git commit -m x") == "write"


def test_timid_uses_classifier():
    assert is_timid_shell_command("ls -la")
    assert is_timid_shell_command("git status")
    assert not is_timid_shell_command("pip install pytest")
    assert is_timid_read_round(
        ["command"],
        [type("T", (), {"name": "command", "arguments": {"command": "head -n 5 a.py"}})()],
    )
