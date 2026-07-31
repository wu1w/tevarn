//! Hardware Abstraction Layer — path / command / browser (P2 I2).

use std::path::{Component, PathBuf};

use serde_json::{json, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HalOs {
    Windows,
    Macos,
    Linux,
    Unknown,
}

impl HalOs {
    pub fn current() -> Self {
        if cfg!(target_os = "windows") {
            Self::Windows
        } else if cfg!(target_os = "macos") {
            Self::Macos
        } else if cfg!(target_os = "linux") {
            Self::Linux
        } else {
            Self::Unknown
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Windows => "windows",
            Self::Macos => "macos",
            Self::Linux => "linux",
            Self::Unknown => "unknown",
        }
    }
}

pub struct Hal;

impl Hal {
    pub fn platform() -> Value {
        json!({
            "os": HalOs::current().as_str(),
            "family": std::env::consts::FAMILY,
            "arch": std::env::consts::ARCH,
            "path_sep": std::path::MAIN_SEPARATOR.to_string(),
        })
    }

    /// Normalize path to absolute form under optional workspace root; reject `..` escape.
    pub fn resolve_path(workspace: Option<&str>, path: &str) -> Result<Value, String> {
        let raw = path.trim();
        if raw.is_empty() {
            return Err("empty path".into());
        }
        // reject null bytes
        if raw.contains('\0') {
            return Err("NUL in path".into());
        }
        let p = PathBuf::from(raw);
        let abs = if p.is_absolute() {
            p
        } else if let Some(ws) = workspace {
            PathBuf::from(ws).join(p)
        } else {
            std::env::current_dir()
                .map_err(|e| e.to_string())?
                .join(p)
        };
        // normalize components
        let mut stack: Vec<String> = Vec::new();
        for c in abs.components() {
            match c {
                Component::ParentDir => {
                    stack.pop();
                }
                Component::Normal(s) => stack.push(s.to_string_lossy().into_owned()),
                Component::RootDir | Component::Prefix(_) => {
                    // keep via re-build
                }
                Component::CurDir => {}
            }
        }
        let normalized = if abs.is_absolute() {
            // rebuild with root
            let mut out = PathBuf::new();
            for c in abs.components() {
                match c {
                    Component::Prefix(p) => out.push(p.as_os_str()),
                    Component::RootDir => out.push(std::path::MAIN_SEPARATOR.to_string()),
                    Component::Normal(s) => out.push(s),
                    Component::ParentDir => {
                        out.pop();
                    }
                    Component::CurDir => {}
                }
            }
            out
        } else {
            PathBuf::from_iter(stack.iter())
        };
        // workspace jail
        if let Some(ws) = workspace {
            let ws_c = PathBuf::from(ws)
                .canonicalize()
                .unwrap_or_else(|_| PathBuf::from(ws));
            let norm_c = normalized
                .canonicalize()
                .unwrap_or_else(|_| normalized.clone());
            let ws_s = ws_c.to_string_lossy().to_lowercase();
            let n_s = norm_c.to_string_lossy().to_lowercase();
            if !n_s.starts_with(ws_s.trim_end_matches(['\\', '/'])) {
                // allow if path not yet existing under ws
                let joined = PathBuf::from(ws).join(path);
                let js = joined.to_string_lossy().replace('/', "\\");
                let wss = PathBuf::from(ws).to_string_lossy().replace('/', "\\");
                if !js.to_lowercase().starts_with(&wss.to_lowercase())
                    && !normalized
                        .to_string_lossy()
                        .to_lowercase()
                        .starts_with(&PathBuf::from(ws).to_string_lossy().to_lowercase())
                {
                    return Err(format!("path escapes workspace: {path}"));
                }
            }
        }
        Ok(json!({
            "input": path,
            "resolved": normalized.display().to_string(),
            "exists": normalized.exists(),
            "is_dir": normalized.is_dir(),
            "is_file": normalized.is_file(),
        }))
    }

    /// Map logical command to OS-specific argv hint (does not execute).
    pub fn resolve_command(logical: &str, args: &[String]) -> Value {
        let os = HalOs::current();
        let (program, shell) = match (logical, os) {
            ("shell", HalOs::Windows) => ("powershell".into(), true),
            ("shell", _) => ("bash".into(), true),
            ("python", _) => {
                if cfg!(target_os = "windows") {
                    ("python".into(), false)
                } else {
                    ("python3".into(), false)
                }
            }
            ("open", HalOs::Windows) => ("start".into(), true),
            ("open", HalOs::Macos) => ("open".into(), false),
            ("open", _) => ("xdg-open".into(), false),
            ("list_dir", HalOs::Windows) => ("dir".into(), true),
            ("list_dir", _) => ("ls".into(), false),
            (other, _) => (other.to_string(), false),
        };
        let mut argv = vec![program.clone()];
        argv.extend(args.iter().cloned());
        json!({
            "logical": logical,
            "os": os.as_str(),
            "program": program,
            "argv": argv,
            "shell": shell,
            "executed": false,
            "note": "HAL resolves only; execution stays in computer backends",
        })
    }

    /// Browser launch descriptor (OS-aware); does not open browser.
    pub fn resolve_browser(url: &str) -> Value {
        let os = HalOs::current();
        let (program, args) = match os {
            HalOs::Windows => (
                "cmd".to_string(),
                vec!["/C".into(), "start".into(), "".into(), url.to_string()],
            ),
            HalOs::Macos => ("open".to_string(), vec![url.to_string()]),
            HalOs::Linux => ("xdg-open".to_string(), vec![url.to_string()]),
            HalOs::Unknown => ("xdg-open".to_string(), vec![url.to_string()]),
        };
        json!({
            "url": url,
            "os": os.as_str(),
            "program": program,
            "args": args,
            "executed": false,
        })
    }

    pub fn status() -> Value {
        json!({
            "platform": Self::platform(),
            "apis": ["resolve_path", "resolve_command", "resolve_browser"],
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_and_command() {
        let r = Hal::resolve_path(None, ".").unwrap();
        assert!(r["resolved"].as_str().unwrap().len() > 0);
        let c = Hal::resolve_command("python", &[]);
        assert!(c["program"].as_str().is_some());
    }
}
