"""Unit tests for thrash-hardening classifiers (deliver/cargo_fix/grep).

No LLM / FS required. Safe rollback: flip agent_* config flags False.
"""

from backend.agent.progress_guard import (
    classify_cargo_error,
    classify_grep_pattern,
    doom_loop_handoff,
    ignored_nudge_action,
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


def test_ignored_nudge_action_no_write_then_force():
    """Live: no_write nudge every even round 6–24; must force, not spam."""
    kw = {"first_at": 6, "grace": 4, "even_only": True}
    assert ignored_nudge_action(current=5, **kw) == "none"
    assert ignored_nudge_action(current=6, **kw) == "nudge"
    assert ignored_nudge_action(current=7, **kw) == "none"
    assert ignored_nudge_action(current=8, **kw) == "nudge"
    assert ignored_nudge_action(current=10, **kw) == "force_final"
    assert ignored_nudge_action(current=24, **kw) == "force_final"


def test_ignored_nudge_action_converge_then_force():
    """Live: converge@16 then 9 more ignored rounds to 25/30."""
    kw = {"first_at": 16, "grace": 2, "every": 10}
    assert ignored_nudge_action(current=15, **kw) == "none"
    assert ignored_nudge_action(current=16, **kw) == "nudge"
    assert ignored_nudge_action(current=17, **kw) == "none"
    assert ignored_nudge_action(current=18, **kw) == "force_final"
    assert ignored_nudge_action(current=25, **kw) == "force_final"
    # Old every=10 without force would still be 'none' at 25 (next nudge at 26)
    assert ignored_nudge_action(current=25, first_at=16, grace=99, every=10) == "none"


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



def test_read_only_check_task_and_status_reads():
    from backend.agent.progress_guard import (
        decide_diag_check_action,
        is_read_only_check_task,
        is_status_read_call,
        record_status_read,
    )

    assert is_read_only_check_task("重新进行基础检测")
    assert is_read_only_check_task("做一次 self-check")
    assert is_read_only_check_task("系统自检")
    assert is_read_only_check_task("检查一下系统状态")
    assert is_read_only_check_task("检查下你自己的工具调用、tevarn自配置、skill这些基础功能都正不正常")
    assert is_read_only_check_task("我改了下代码，你复测一轮")
    assert is_read_only_check_task("再测一轮")
    assert not is_read_only_check_task("帮我写一个排序算法")
    assert not is_read_only_check_task("修编译错误 E0433")
    assert not is_read_only_check_task("帮我检查这段代码的 bug")

    assert is_status_read_call("configure_tevarn", {"action": "status"})
    assert is_status_read_call("configure_tevarn", {"action": "guide", "topic": "models"})
    assert is_status_read_call("manage_skill", {"action": "list"})
    assert is_status_read_call("get_system_status", {})
    assert is_status_read_call("crew_steward", {"action": "list"})
    assert not is_status_read_call("configure_tevarn", {"action": "set_setting", "key": "x"})
    assert not is_status_read_call("manage_skill", {"action": "create", "name": "x"})
    assert not is_status_read_call("file_write", {"path": "a.py"})
    assert not is_status_read_call("file_read", {"path": "a.py"})
    assert not is_status_read_call("grep", {"pattern": "foo"})
    assert not is_status_read_call("glob", {"glob": "*.py"})

    assert decide_diag_check_action(
        user_input="重新进行基础检测",
        wrote=False,
        diag_probe_rounds=1,
        duplicate_status=False,
    ) == "skip_no_write"
    assert decide_diag_check_action(
        user_input="重新进行基础检测",
        wrote=False,
        diag_probe_rounds=3,
        duplicate_status=False,
    ) == "force_final"
    # Duplicate status alone must not wrap — that chopped long tasks.
    assert decide_diag_check_action(
        user_input="重新进行基础检测",
        wrote=False,
        diag_probe_rounds=1,
        duplicate_status=True,
    ) == "skip_no_write"
    assert decide_diag_check_action(
        user_input="帮我写一个排序",
        wrote=False,
        had_work=False,
        diag_probe_rounds=5,
        duplicate_status=True,
    ) == "force_final"
    assert decide_diag_check_action(
        user_input="再测一轮",
        wrote=False,
        had_work=True,
        diag_probe_rounds=5,
        duplicate_status=True,
    ) == "none"
    assert decide_diag_check_action(
        user_input="继续",
        wrote=False,
        had_work=False,
        had_work_this_run=True,
        diag_probe_rounds=5,
        duplicate_status=True,
    ) == "none"
    assert decide_diag_check_action(
        user_input="帮我写一个排序",
        wrote=False,
        had_work=False,
        diag_probe_rounds=3,
        duplicate_status=False,
    ) == "force_final"
    assert decide_diag_check_action(
        user_input="重新进行基础检测",
        wrote=True,
        diag_probe_rounds=3,
        duplicate_status=True,
    ) == "none"

    counts: dict[str, int] = {}
    assert record_status_read(counts, "configure_tevarn", {"action": "status"}) is False
    assert record_status_read(counts, "configure_tevarn", {"action": "status"}) is True
    # different topic is a new signature
    assert record_status_read(
        counts, "configure_tevarn", {"action": "status", "topic": "models"}
    ) is False


def test_rust_diag_command_not_file_body():
    from backend.agent.progress_guard import is_rust_diag_command

    assert is_rust_diag_command("rustup default stable")
    assert is_rust_diag_command("where cargo")
    assert is_rust_diag_command("cargo -V")
    assert not is_rust_diag_command("cargo check -p tevarn-kernel")
    assert not is_rust_diag_command("python -m pytest backend/tests")
    assert not is_rust_diag_command("pytest backend/tests/test_progress_guard_thrash.py")


def test_round_did_work_and_had_work_this_run():
    from backend.agent.progress_guard import (
        decide_diag_check_action,
        round_did_work,
    )

    assert not round_did_work([("configure_tevarn", {"action": "status"})])
    assert not round_did_work([("manage_skill", {"action": "list"})])
    assert round_did_work([("command", {"cmd": "pytest"})])
    assert round_did_work([("tavily_search", {"query": "x"})])
    assert round_did_work([("file_read", {"path": "a.py"})])
    assert round_did_work(
        [
            ("configure_tevarn", {"action": "status"}),
            ("command", {"cmd": "pytest -q"}),
        ]
    )
    # After work earlier this run, later status dups never wrap.
    assert decide_diag_check_action(
        user_input="[System auto-resume] Continue the unfinished Goal",
        wrote=False,
        had_work=False,
        had_work_this_run=True,
        diag_probe_rounds=3,
        duplicate_status=True,
    ) == "none"
