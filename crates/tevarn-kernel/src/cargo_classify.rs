//! Pure cargo failure classifier (parity with Python `progress_guard.classify_cargo_error`).
//!
//! Used as a hot-path RPC (`cargo_classify`) so thrash arming policy can run in Rust
//! without pulling Python regex. No process state — pure function of the result text.

use serde_json::{json, Value};

/// Classify cargo / rustc tool output.
///
/// Returns: `compile_source` | `path_env` | `toolchain` | `linker_env` | `unknown` | `ok`
/// Only `compile_source` should arm must_write_before_cargo.
pub fn classify_cargo_error(result: &str) -> &'static str {
    let t = result;
    if t.trim().is_empty() {
        return "unknown";
    }
    let lower = t.to_ascii_lowercase();

    if is_metadata_stub(&lower) {
        return "toolchain";
    }
    if is_linker_env(&lower) {
        return "linker_env";
    }
    if is_path_env(&lower) {
        return "path_env";
    }
    // Strong source signals first
    if has_error_e_code(t) || has_rustc_path_arrow(t) {
        return "compile_source";
    }
    if is_source_err(&lower) && !is_path_env(&lower) {
        return "compile_source";
    }
    if looks_ok(&lower) && !has_error_e_code(t) {
        return "ok";
    }
    // Exit 101 alone (no E-code / --> path / could not compile) → unknown
    if has_bare_exit_101(&lower) {
        return "unknown";
    }
    "unknown"
}

pub fn classify_cargo_error_json(result: &str) -> Value {
    let class = classify_cargo_error(result);
    json!({
        "class": class,
        "kind": class,
        "compile_source": class == "compile_source",
        "ok": class == "ok",
    })
}

fn is_metadata_stub(lower: &str) -> bool {
    lower.contains("only metadata stub found")
        || lower.contains("metadata stub")
        || lower.contains("missing manifest in toolchain")
}

fn is_linker_env(lower: &str) -> bool {
    lower.contains("link.exe")
        || lower.contains("lnk1104")
        || lower.contains("lnk1181")
        || lower.contains("lnk2019")
        || (lower.contains("linker") && lower.contains("not found"))
        || lower.contains("unable to find utility") && lower.contains("link")
        || (lower.contains("msvc")
            && (lower.contains("not found") || lower.contains("not installed")))
}

fn is_path_env(lower: &str) -> bool {
    lower.contains("failed to load manifest")
        || lower.contains("could not find `cargo.toml`")
        || lower.contains("could not find 'cargo.toml'")
        || lower.contains("could not find cargo.toml")
        || lower.contains("manifest path") && lower.contains("does not exist")
        || lower.contains("no such file or directory")
        || lower.contains("is not a member of the workspace")
        || lower.contains("is not a member of workspace")
        || lower.contains("current package believes it's in a workspace")
        || lower.contains("cwd does not exist")
        || (lower.contains("does not exist")
            && (lower.contains("crates") || lower.contains("cargo.toml")))
}

fn has_error_e_code(t: &str) -> bool {
    // error[E0433] etc.
    let bytes = t.as_bytes();
    let n = bytes.len();
    let mut i = 0;
    while i + 8 < n {
        // case-insensitive "error[E"
        if (bytes[i] == b'e' || bytes[i] == b'E')
            && (bytes[i + 1] == b'r' || bytes[i + 1] == b'R')
            && (bytes[i + 2] == b'r' || bytes[i + 2] == b'R')
            && (bytes[i + 3] == b'o' || bytes[i + 3] == b'O')
            && (bytes[i + 4] == b'r' || bytes[i + 4] == b'R')
            && bytes[i + 5] == b'['
            && (bytes[i + 6] == b'E' || bytes[i + 6] == b'e')
            && bytes[i + 7].is_ascii_digit()
        {
            return true;
        }
        i += 1;
    }
    false
}

fn has_rustc_path_arrow(t: &str) -> bool {
    // --> path\to\file.rs:line
    for line in t.lines() {
        let s = line.trim_start();
        if !s.starts_with("-->") {
            continue;
        }
        let rest = s.trim_start_matches("-->").trim_start();
        let path_part = rest.split(':').next().unwrap_or("");
        let lower = path_part.to_ascii_lowercase();
        if lower.ends_with(".rs") || lower.ends_with(".toml") {
            return true;
        }
    }
    false
}

fn is_source_err(lower: &str) -> bool {
    lower.contains("error[e")
        || lower.contains("--> ") && lower.contains(".rs:")
        || lower.contains("could not compile")
        || lower.contains("error: could not compile")
        || lower.contains("aborting due to")
}

fn looks_ok(lower: &str) -> bool {
    (lower.contains("finished")
        || lower.contains("status=done exit=0")
        || lower.contains("[exit 0")
        || lower.contains("[exit  0"))
        && !lower.contains("error[")
}

fn has_bare_exit_101(lower: &str) -> bool {
    lower.contains("status=done exit=101")
        || lower.contains("[exit 101")
        || lower.contains("exit code 101")
        || lower.contains("exitcode 101")
        || lower.contains("exit_code 101")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_e_code() {
        let src = "error[E0433]: cannot find type `Foo`\n  --> crates/guardian-types/src/lib.rs:10:5\nerror: could not compile `guardian-types`\n";
        assert_eq!(classify_cargo_error(src), "compile_source");
    }

    #[test]
    fn path_env_manifest() {
        let path = "error: failed to load manifest for workspace member `C:\\Users\\x\\workspace\\crates/guardian-types`\nreferenced by workspace at ...\\Cargo.toml\nstatus=done exit=101\n";
        assert_eq!(classify_cargo_error(path), "path_env");
    }

    #[test]
    fn bare_exit_101_unknown() {
        let bare = "[bg bg_abc] status=done exit=101\ncommand: cargo check -p guardian-types\ncwd: C:\\ws\n";
        assert_eq!(classify_cargo_error(bare), "unknown");
    }

    #[test]
    fn toolchain_stub() {
        let stub = "error: only metadata stub found for `rlib` dependency `core`";
        assert_eq!(classify_cargo_error(stub), "toolchain");
    }

    #[test]
    fn ok_finished() {
        let ok = "Finished `dev` profile [unoptimized + debuginfo] target(s) in 1.2s\nstatus=done exit=0\n";
        assert_eq!(classify_cargo_error(ok), "ok");
    }
}
