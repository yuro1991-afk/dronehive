//! Ollama ensure + API client (wired for the installed app).

use serde::Deserialize;
use serde_json::json;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

pub const DEFAULT_HOST: &str = "http://127.0.0.1:11434";

#[derive(Debug, Clone)]
pub struct OllamaStatus {
    pub ok: bool,
    pub detail: String,
    pub models: Vec<String>,
    pub version: Option<String>,
}

#[derive(Debug, Deserialize)]
struct TagsResp {
    models: Vec<ModelTag>,
}

#[derive(Debug, Deserialize)]
struct ModelTag {
    name: String,
}

pub fn ensure_ollama() -> OllamaStatus {
    let st = status(DEFAULT_HOST);
    if st.ok {
        return st;
    }
    // Host-critical ensure (BOSS)
    let ensure = PathBuf::from(std::env::var("USERPROFILE").unwrap_or_default())
        .join(".ollama")
        .join("Ensure-OllamaCritical.ps1");
    let keep = PathBuf::from(std::env::var("USERPROFILE").unwrap_or_default())
        .join(".ollama")
        .join("start-ollama-serve-keep.ps1");
    let script = if ensure.is_file() {
        ensure
    } else if keep.is_file() {
        keep
    } else {
        return OllamaStatus {
            ok: false,
            detail: "Ollama down; no Ensure/keep script found".into(),
            models: vec![],
            version: None,
        };
    };
    // Soft ensure: do not hang UI worker forever; CREATE_NO_WINDOW on Windows via powershell -WindowStyle Hidden
    let mut child = Command::new("powershell")
        .args([
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            &script.to_string_lossy(),
        ])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .ok();
    if let Some(ref mut c) = child {
        // max wait 45s then abandon child (don't kill user Ollama)
        let deadline = std::time::Instant::now() + Duration::from_secs(45);
        loop {
            match c.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if std::time::Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(200));
                }
                _ => break,
            }
        }
    }
    std::thread::sleep(Duration::from_secs(1));
    status(DEFAULT_HOST)
}

pub fn status(host: &str) -> OllamaStatus {
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(2))
        .timeout_read(Duration::from_secs(8))
        .build();
    let version = agent
        .get(&format!("{}/api/version", host.trim_end_matches('/')))
        .call()
        .ok()
        .and_then(|r| r.into_json::<serde_json::Value>().ok())
        .and_then(|v| v.get("version").and_then(|x| x.as_str()).map(|s| s.to_string()));

    match agent
        .get(&format!("{}/api/tags", host.trim_end_matches('/')))
        .call()
    {
        Ok(resp) => match resp.into_json::<TagsResp>() {
            Ok(tags) => {
                let models: Vec<String> = tags.models.into_iter().map(|m| m.name).collect();
                OllamaStatus {
                    ok: true,
                    detail: format!("{} models", models.len()),
                    models,
                    version,
                }
            }
            Err(e) => OllamaStatus {
                ok: false,
                detail: format!("tags parse: {e}"),
                models: vec![],
                version,
            },
        },
        Err(e) => OllamaStatus {
            ok: false,
            detail: e.to_string(),
            models: vec![],
            version,
        },
    }
}

pub fn chat(
    host: &str,
    model: &str,
    messages: &[(String, String)],
    system: &str,
) -> Result<(String, u128), String> {
    let t0 = std::time::Instant::now();
    let mut msgs = Vec::new();
    if !system.trim().is_empty() {
        msgs.push(json!({"role":"system","content": system}));
    }
    for (role, content) in messages {
        msgs.push(json!({"role": role, "content": content}));
    }
    let body = json!({
        "model": model,
        "messages": msgs,
        "stream": false,
        "options": {"num_predict": 512}
    });
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(3))
        .timeout_read(Duration::from_secs(180))
        .build();
    let resp: serde_json::Value = agent
        .post(&format!("{}/api/chat", host.trim_end_matches('/')))
        .send_json(body)
        .map_err(|e| e.to_string())?
        .into_json()
        .map_err(|e| e.to_string())?;
    let text = resp
        .pointer("/message/content")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if text.trim().is_empty() {
        if let Some(err) = resp.get("error").and_then(|e| e.as_str()) {
            return Err(err.to_string());
        }
        return Err("empty model response".into());
    }
    Ok((text, t0.elapsed().as_millis()))
}

/// Ollama as COMMANDER: turn user input into a drone task pack (JSON).
/// Returns (raw_text, parsed_plan, ms).
pub fn commander_plan(
    host: &str,
    model: &str,
    user_goal: &str,
) -> Result<(String, CommanderPlan, u128), String> {
    let system = r#"You are the OLLAMA COMMANDER for a dual-hemisphere fabric of 24 controllable worker drones (NOT full LLMs per drone).
Your job: convert the user goal into a task pack the drones will execute.

Reply with ONLY valid JSON (no markdown fences):
{
  "mission": "one-line mission title",
  "brief": "clear task brief every drone must follow (2-6 sentences)",
  "subtasks": ["optional hop 1", "optional hop 2"],
  "mode": "fabric",
  "skill_tags": ["build"],
  "delegate_all_drones": true
}
mode is "fabric" (one L||R pass of 24) or "swarm" (multiple goals fan-out).
Keep brief concrete and actionable. No chat fluff."#;

    let (text, ms) = chat(
        host,
        model,
        &[("user".into(), user_goal.to_string())],
        system,
    )?;
    let plan = CommanderPlan::parse(&text, user_goal);
    Ok((text, plan, ms))
}

#[derive(Debug, Clone)]
pub struct CommanderPlan {
    pub mission: String,
    pub brief: String,
    pub subtasks: Vec<String>,
    pub mode: String, // fabric | swarm
    pub skill_tags: Vec<String>,
    pub delegate_all_drones: bool,
    pub composed_goal: String,
}

impl CommanderPlan {
    pub fn parse(raw: &str, fallback_goal: &str) -> Self {
        // extract JSON object if model wrapped text
        let json_slice = extract_json_object(raw).unwrap_or_else(|| raw.to_string());
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&json_slice) {
            let mission = v
                .get("mission")
                .and_then(|x| x.as_str())
                .unwrap_or("mission")
                .to_string();
            let brief = v
                .get("brief")
                .and_then(|x| x.as_str())
                .unwrap_or(fallback_goal)
                .to_string();
            let subtasks: Vec<String> = v
                .get("subtasks")
                .and_then(|x| x.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|i| i.as_str().map(|s| s.to_string()))
                        .filter(|s| !s.trim().is_empty())
                        .collect()
                })
                .unwrap_or_default();
            let mode = v
                .get("mode")
                .and_then(|x| x.as_str())
                .unwrap_or("fabric")
                .to_string()
                .to_lowercase();
            let skill_tags: Vec<String> = v
                .get("skill_tags")
                .and_then(|x| x.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|i| i.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_else(|| vec!["build".into()]);
            let delegate_all_drones = v
                .get("delegate_all_drones")
                .and_then(|x| x.as_bool())
                .unwrap_or(true);
            let composed_goal = format!(
                "{fallback_goal} | COMMANDER_MISSION: {mission} | COMMANDER_BRIEF: {brief}"
            );
            return Self {
                mission,
                brief,
                subtasks,
                mode: if mode == "swarm" {
                    "swarm".into()
                } else {
                    "fabric".into()
                },
                skill_tags,
                delegate_all_drones,
                composed_goal,
            };
        }
        // fallback: use raw as brief
        let brief = raw.chars().take(800).collect::<String>();
        Self {
            mission: "user_goal".into(),
            brief: brief.clone(),
            subtasks: vec![],
            mode: "fabric".into(),
            skill_tags: vec!["build".into()],
            delegate_all_drones: true,
            composed_goal: format!("{fallback_goal} | COMMANDER_BRIEF: {brief}"),
        }
    }
}

fn extract_json_object(s: &str) -> Option<String> {
    let start = s.find('{')?;
    let slice = &s[start..];
    let mut depth = 0i32;
    let mut end = None;
    for (i, ch) in slice.char_indices() {
        match ch {
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    end = Some(i);
                    break;
                }
            }
            _ => {}
        }
    }
    let end = end?;
    Some(slice[..=end].to_string())
}

/// Prefer quality installed model for controller.
pub fn pick_default_model(models: &[String]) -> String {
    const PREF: &[&str] = &[
        "llama3.1:8b",
        "qwen2.5-coder:7b",
        "qwen2.5:7b",
        "mistral:7b",
        "llama3.2:3b",
        "qwen2.5:3b",
    ];
    for p in PREF {
        if models.iter().any(|m| m == p || m.starts_with(&format!("{p}-"))) {
            return (*p).to_string();
        }
    }
    models.first().cloned().unwrap_or_else(|| "llama3.2:3b".into())
}
