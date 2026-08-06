//! Open-source skill system (SKILL.md compatible).
//! Layout: `{data_dir}/skills/<id>/SKILL.md` with YAML frontmatter.

use crate::error::{Error, Result};
use crate::storage::Store;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

const SKILLS_DIR: &str = "skills";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillMeta {
    pub id: String,
    pub name: String,
    pub description: String,
    #[serde(default)]
    pub triggers: Vec<String>,
    #[serde(default)]
    pub version: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Skill {
    pub meta: SkillMeta,
    pub body: String,
    pub path: String,
}

pub struct SkillStore {
    root: PathBuf,
}

impl SkillStore {
    pub fn new(store: &Store) -> Self {
        let root = store.root().join(SKILLS_DIR);
        let _ = std::fs::create_dir_all(&root);
        // Seed builtins once
        seed_builtins(&root);
        Self { root }
    }

    pub fn from_path(root: PathBuf) -> Self {
        let _ = std::fs::create_dir_all(&root);
        seed_builtins(&root);
        Self { root }
    }

    pub fn list(&self) -> Vec<SkillMeta> {
        let mut out = Vec::new();
        let Ok(rd) = std::fs::read_dir(&self.root) else {
            return out;
        };
        for e in rd.flatten() {
            let p = e.path();
            let skill_md = if p.is_dir() {
                p.join("SKILL.md")
            } else if p.extension().and_then(|x| x.to_str()) == Some("md") {
                p.clone()
            } else {
                continue;
            };
            if let Ok(s) = load_skill_file(&skill_md) {
                out.push(s.meta);
            }
        }
        out.sort_by(|a, b| a.id.cmp(&b.id));
        out
    }

    pub fn get(&self, id: &str) -> Result<Skill> {
        let id = id.trim();
        // dir form
        let p1 = self.root.join(id).join("SKILL.md");
        if p1.is_file() {
            return load_skill_file(&p1);
        }
        let p2 = self.root.join(format!("{id}.md"));
        if p2.is_file() {
            return load_skill_file(&p2);
        }
        // search by name
        for m in self.list() {
            if m.id == id || m.name == id {
                return self.get(&m.id);
            }
        }
        Err(Error::Msg(format!("skill not found: {id}")))
    }

    pub fn match_for_prompt(&self, user_text: &str) -> Vec<Skill> {
        let lower = user_text.to_lowercase();
        let mut hits = Vec::new();
        for m in self.list() {
            let mut score = 0;
            if lower.contains(&m.name.to_lowercase()) {
                score += 2;
            }
            for t in &m.triggers {
                if !t.is_empty() && (lower.contains(&t.to_lowercase()) || user_text.contains(t)) {
                    score += 3;
                }
            }
            if score > 0 {
                if let Ok(s) = self.get(&m.id) {
                    hits.push((score, s));
                }
            }
        }
        hits.sort_by(|a, b| b.0.cmp(&a.0));
        hits.into_iter().take(2).map(|(_, s)| s).collect()
    }

    pub fn prompt_block(&self, skills: &[Skill]) -> String {
        if skills.is_empty() {
            return String::new();
        }
        let mut s = String::from("【已激活 Skills · 按正文指引行动】\n");
        for sk in skills {
            s.push_str(&format!(
                "### skill:{} — {}\n{}\n\n",
                sk.meta.id,
                sk.meta.description,
                sk.body.chars().take(2500).collect::<String>()
            ));
        }
        s
    }

    pub fn list_json(&self) -> Value {
        json!(self.list())
    }

    /// Install / overwrite a skill from full SKILL.md content.
    pub fn install_content(&self, id: &str, content: &str) -> Result<String> {
        let id = id
            .trim()
            .replace(['/', '\\', ' ', ':'], "-")
            .trim_matches(|c| c == '.' || c == '-')
            .to_string();
        if id.is_empty() {
            return Err(Error::Msg("empty skill id".into()));
        }
        let dir = self.root.join(&id);
        std::fs::create_dir_all(&dir).map_err(|e| Error::Msg(e.to_string()))?;
        let path = dir.join("SKILL.md");
        std::fs::write(&path, content).map_err(|e| Error::Msg(e.to_string()))?;
        Ok(path.display().to_string())
    }

    /// Remove an installed skill directory.
    pub fn uninstall(&self, id: &str) -> Result<bool> {
        let id = id.trim();
        let dir = self.root.join(id);
        if dir.is_dir() {
            std::fs::remove_dir_all(&dir).map_err(|e| Error::Msg(e.to_string()))?;
            return Ok(true);
        }
        let md = self.root.join(format!("{id}.md"));
        if md.is_file() {
            std::fs::remove_file(&md).map_err(|e| Error::Msg(e.to_string()))?;
            return Ok(true);
        }
        Ok(false)
    }
}

/// Known Matt Pocock mobile pack: (id, raw github path category/name)
pub fn mattpocock_mobile_pack() -> &'static [(&'static str, &'static str)] {
    &[
        ("grill-me", "productivity/grill-me"),
        ("handoff", "productivity/handoff"),
        ("wait-what", "productivity/wait-what"),
        ("writing-for-agents", "productivity/writing-for-agents"),
        ("research", "engineering/research"),
        ("diagnosing-bugs", "engineering/diagnosing-bugs"),
        ("tdd", "engineering/tdd"),
        ("code-review", "engineering/code-review"),
        ("to-spec", "engineering/to-spec"),
    ]
}

pub fn mattpocock_raw_url(category_name: &str) -> String {
    format!(
        "https://raw.githubusercontent.com/mattpocock/skills/main/skills/{category_name}/SKILL.md"
    )
}


fn load_skill_file(path: &Path) -> Result<Skill> {
    let raw = std::fs::read_to_string(path).map_err(|e| Error::Msg(e.to_string()))?;
    let (meta, body) = parse_frontmatter(&raw, path);
    Ok(Skill {
        meta,
        body,
        path: path.display().to_string(),
    })
}

fn parse_frontmatter(raw: &str, path: &Path) -> (SkillMeta, String) {
    let default_id = path
        .parent()
        .and_then(|p| p.file_name())
        .and_then(|s| s.to_str())
        .filter(|s| *s != "skills")
        .unwrap_or_else(|| {
            path.file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("skill")
        })
        .to_string();

    if raw.starts_with("---") {
        if let Some(end) = raw[3..].find("\n---") {
            let yaml = &raw[3..3 + end];
            let body = raw[3 + end + 4..].trim_start_matches('\n').to_string();
            let mut name = default_id.clone();
            let mut description = String::new();
            let mut triggers = Vec::new();
            let mut version = String::new();
            let mut id = default_id.clone();
            for line in yaml.lines() {
                let line = line.trim();
                if let Some(v) = line.strip_prefix("name:") {
                    name = v.trim().trim_matches('"').to_string();
                    id = name.clone();
                } else if let Some(v) = line.strip_prefix("description:") {
                    description = v.trim().trim_matches('"').to_string();
                } else if let Some(v) = line.strip_prefix("version:") {
                    version = v.trim().trim_matches('"').to_string();
                } else if let Some(v) = line.strip_prefix("id:") {
                    id = v.trim().trim_matches('"').to_string();
                } else if let Some(v) = line.strip_prefix("triggers:") {
                    let v = v.trim();
                    if v.starts_with('[') {
                        for p in v
                            .trim_matches(|c| c == '[' || c == ']')
                            .split(',')
                        {
                            let t = p.trim().trim_matches('"').to_string();
                            if !t.is_empty() {
                                triggers.push(t);
                            }
                        }
                    }
                } else if let Some(v) = line.strip_prefix("- ") {
                    if !triggers.is_empty() || true {
                        // list form under triggers key — accept any dash list in frontmatter
                        let t = v.trim().trim_matches('"').to_string();
                        if !t.is_empty() && !t.contains(':') {
                            triggers.push(t);
                        }
                    }
                }
            }
            // multi-line description after description: >
            if description.is_empty() {
                description = name.clone();
            }
            return (
                SkillMeta {
                    id,
                    name,
                    description,
                    triggers,
                    version,
                },
                body,
            );
        }
    }
    (
        SkillMeta {
            id: default_id.clone(),
            name: default_id,
            description: "user skill".into(),
            triggers: vec![],
            version: String::new(),
        },
        raw.to_string(),
    )
}

fn seed_builtins(root: &Path) {
    let builtins: &[(&str, &str)] = &[
        (
            "research/SKILL.md",
            r#"---
name: research
description: 多源调研与带引用的结论
triggers: [调研, 研究, research, 分析一下, 对比]
version: "1.0"
---
# Research skill
1. 用 web_search 收集 3+ 来源；关键细节用 web_fetch。
2. 交叉验证矛盾信息；标注不确定。
3. 输出：结论 → 证据要点（带来源 URL）→ 风险/未知。
4. 禁止编造链接或数据。
"#,
        ),
        (
            "coding/SKILL.md",
            r#"---
name: coding
description: 编程辅助：解释、改写、排错
triggers: [代码, 编程, bug, 报错, refactor, code]
version: "1.0"
---
# Coding skill
1. 先确认语言/运行环境；缺信息就问一句。
2. 给可运行的最小改动；标出关键 diff。
3. 复杂题拆步；需要计算用 calculator。
4. 安全：不建议危险命令。
"#,
        ),
        (
            "daily/SKILL.md",
            r#"---
name: daily
description: 日常助理：日程语气、备忘、朗读
triggers: [提醒, 备忘, 朗读, 翻译, todo]
version: "1.0"
---
# Daily skill
1. 短备忘用 memory_note。
2. 需要出声用 voice_speak。
3. 时间相关用 get_datetime。
4. 回复简短可执行。
"#,
        ),
        (
            "codex-security/SKILL.md",
            r#"---
name: codex-security
description: 安全扫描指引（OpenAI Codex Security CLI）。用户要求漏洞/AppSec 时使用。
triggers: [安全扫描, 漏洞, AppSec, security, codex-security, OWASP]
version: "1.0"
---
# Codex Security（手机轻量指引）
完整扫描优先在 PC 用 `npx -y @openai/codex-security scan .`。
手机端：
1. 明确范围与是否允许联网。
2. 用 file_read / grep 做手工安全清单（注入、鉴权、密钥、SSRF、路径穿越）。
3. 不要编造扫描报告；若无法跑 CLI 要明确说明。
4. 修复建议要最小可落地。
"#,
        ),
    ];
    for (rel, body) in builtins {
        let p = root.join(rel);
        if !p.exists() {
            if let Some(parent) = p.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = std::fs::write(p, body);
        }
    }
}
