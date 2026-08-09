"""Unit tests for thrash-hardening classifiers (deliver/cargo_fix/grep).

No LLM / FS required. Safe rollback: flip agent_* config flags False.
"""

from backend.agent.progress_guard import (
    classify_cargo_error,
    classify_grep_pattern,
    doom_loop_handoff,
    is_cargo_compile_failure,
    is_deliver_allowed_command,
    is_deliver_allowed_grep,
    is_diag_junk_path,
    is_probe_overwrite,
    is_review_only_task,
    should_arm_deliver_mode,
)


def test_grep_whole_file_vs_narrow():
    assert classify_grep_pattern(".*") == "whole_file"
    assert classify_grep_pattern(".+") == "whole_file"
    assert classify_grep_pattern(r"[\s\S]*") == "whole_file"
    assert classify_grep_pattern("^") == "whole_file"
    assert classify_grep_pattern(".") == "whole_file"
    assert classify_grep_pattern("") == "empty"
    assert classify_grep_pattern(r"error\[E0433\]") == "narrow"
    assert classify_grep_pattern("GuardianError") == "narrow"
    assert classify_grep_pattern("fn create_tenant") == "narrow"
    assert not is_deliver_allowed_grep(".*")
    assert not is_deliver_allowed_grep("^")
    assert is_deliver_allowed_grep(r"error\[E0433\]")
    assert is_deliver_allowed_grep("GuardianError")


def test_cargo_error_class_source_vs_path():
    src = (
        "error[E0433]: cannot find type `Foo` in this scope\n"
        "  --> crates/guardian-types/src/lib.rs:10:5\n"
        "error: could not compile `guardian-types`\n"
    )
    assert classify_cargo_error(src) == "compile_source"
    assert is_cargo_compile_failure(src) is True

    path = (
        "error: failed to load manifest for workspace member "
        "`C:\\Users\\x\\workspace\\crates/guardian-types`\n"
        "referenced by workspace at ...\\Cargo.toml\n"
        "status=done exit=101\n"
    )
    assert classify_cargo_error(path) == "path_env"
    assert is_cargo_compile_failure(path) is False

    bare = (
        "[bg bg_abc] status=done exit=101\n"
        "command: cargo check -p guardian-types\n"
        "cwd: C:\\ws\n"
    )
    # bare 101 without E-code / could not compile → do not arm write-gate
    assert classify_cargo_error(bare) in ("unknown", "path_env")
    assert is_cargo_compile_failure(bare) is False

    stub = "error: only metadata stub found for `rlib` dependency `core`"
    assert classify_cargo_error(stub) == "toolchain"
    assert is_cargo_compile_failure(stub) is False


def test_review_only_and_deliver_arm():
    assert is_review_only_task("复查以下你做的所有源码，质量堪忧啊") is True
    assert is_review_only_task("对 guardian 做 code review") is True
    assert is_review_only_task("修编译错误 E0433") is False
    assert is_review_only_task("对齐编译 cargo check") is False

    assert (
        should_arm_deliver_mode("复查源码质量", reason="pure_read") is False
    )
    assert (
        should_arm_deliver_mode("修编译 error[E0433]", reason="pure_read") is True
    )
    assert (
        should_arm_deliver_mode("随便聊聊", reason="cargo_fix") is True
    )
    assert (
        should_arm_deliver_mode("code review", reason="file_read_cap") is False
    )


def test_diag_junk_and_probe():
    assert is_diag_junk_path("_review_list.py")
    assert is_diag_junk_path("tavarn-guardian/_rs_index.txt")
    assert is_diag_junk_path("_list_rs.py")
    assert is_diag_junk_path("_tmp_list.txt")
    assert not is_diag_junk_path("crates/guardian-types/src/lib.rs")

    assert is_probe_overwrite(
        "crates/guardian-types/src/lib.rs",
        "// probe\n",
    )
    assert is_probe_overwrite(
        "crates/foo/src/lib.rs",
        'fn main(){ println!("hello"); }\n',
    )
    real = (
        "//! domain types\n"
        "pub struct Tenant { pub id: u64 }\n"
        "impl Tenant { pub fn new() -> Self { Self { id: 0 } } }\n"
    )
    assert not is_probe_overwrite("crates/guardian-types/src/lib.rs", real)


def test_deliver_shell_still_blocked():
    assert not is_deliver_allowed_command("Get-Content foo.rs")
    assert not is_deliver_allowed_command("cmd /c dir /s /b")
    assert is_deliver_allowed_command("cargo check -p guardian-server")


def test_doom_handoff_short():
    msg = doom_loop_handoff(
        deliver_mode=True,
        must_write=True,
        cargo_paths="crates/foo/src/lib.rs",
        cargo_class="compile_source",
        last_tools="grep,grep,command",
    )
    assert "熔断" in msg
    assert "crates/foo" in msg
    assert len(msg) < 600
    path_msg = doom_loop_handoff(cargo_class="path_env")
    assert "路径" in path_msg or "锚点" in path_msg


def test_blocked_with_next_menu():
    from backend.agent.progress_guard import blocked_with_next, cargo_fix_nudge

    b = blocked_with_next(
        "[Blocked] 编译错误未修。",
        "must_write_blocks_cargo",
        paths="crates/foo/src/lib.rs",
    )
    assert "NEXT:" in b
    assert "file_write" in b or "edit" in b
    n = cargo_fix_nudge(["crates/a/src/lib.rs"])
    assert "NEXT:" in n
    assert "crates/a" in n


def test_family_bucket_process_not_cargo():
    from backend.agent.decisive import family_bucket

    class T:
        def __init__(self, name, arguments=None):
            self.name = name
            self.arguments = arguments or {}

    # pure process polls → process_poll, NOT cargo_verify
    fam = family_bucket([T("process", {"action": "poll", "process_id": "bg_x"})])
    assert fam == "process_poll"
    # real cargo command still cargo_verify
    fam2 = family_bucket(
        [T("command", {"command": "cargo check -p guardian-types"})]
    )
    assert fam2 == "cargo_verify"


def test_poll_throttle_helpers():
    import time
    from backend.services.tools.process_registry import BgProcess, poll_process_throttled

    p = BgProcess(
        id="bg_test1",
        command="cargo check",
        cwd="C:\\ws",
        started_at=time.time(),
        done=False,
    )
    r1 = poll_process_throttled(p)
    assert "status=running" in r1
    assert p.poll_count_running == 1
    # immediate re-poll without new output → throttle
    r2 = poll_process_throttled(p)
    assert "Poll throttle" in r2 or "throttle" in r2.lower() or "Blocked" in r2
