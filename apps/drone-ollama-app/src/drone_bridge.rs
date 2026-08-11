//! Bridge to Python drone fabric + optional rust mount CLI.

use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;

pub const DRONE_ROOT: &str = r"G:\AI-Home\projects\ai-worker-drone-0.5b";
pub const MOUNT_EXE: &str =
    r"G:\AI-Home\projects\drone-ollama-mount\target\release\drone-ollama-mount.exe";

fn python() -> PathBuf {
    let cands = [
        PathBuf::from(r"C:\Users\yuro1\AppData\Local\Programs\Python\Python312\python.exe"),
        PathBuf::from(r"C:\Python312\python.exe"),
    ];
    for c in cands {
        if c.exists() {
            return c;
        }
    }
    PathBuf::from("python")
}

fn parse_json(stdout: &str) -> Result<Value, String> {
    if let Ok(v) = serde_json::from_str::<Value>(stdout) {
        return Ok(v);
    }
    let start = stdout
        .find('{')
        .ok_or_else(|| format!("no JSON in: {}", &stdout[..stdout.len().min(200)]))?;
    let slice = &stdout[start..];
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
    let end = end.ok_or_else(|| "unbalanced JSON".to_string())?;
    serde_json::from_str(&slice[..=end]).map_err(|e| e.to_string())
}

pub fn drone_info() -> Result<Value, String> {
    run_drone(&["info"])
}

pub fn drone_run(goal: &str, controller: &str, lm_assist: &str) -> Result<Value, String> {
    run_drone(&[
        "run",
        "--goal",
        goal,
        "--controller",
        controller,
        "--lm-assist",
        lm_assist,
    ])
}

pub fn drone_swarm(goals: &[String], workers: u32, controller: &str) -> Result<Value, String> {
    // fabric-swarm if present; else swarm
    let mut args: Vec<String> = vec![
        "fabric-swarm".into(),
        "--controller".into(),
        controller.into(),
        "--workers".into(),
        workers.to_string(),
        "--lm-assist".into(),
        "none".into(),
    ];
    for g in goals {
        args.push("--goal".into());
        args.push(g.clone());
    }
    let refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    match run_drone(&refs) {
        Ok(v) => Ok(v),
        Err(_) => {
            // fallback multi-goal via hive swarm name
            let mut args2: Vec<String> = vec![
                "swarm".into(),
                "--controller".into(),
                controller.into(),
                "--workers".into(),
                workers.to_string(),
            ];
            for g in goals {
                args2.push("--goal".into());
                args2.push(g.clone());
            }
            let refs2: Vec<&str> = args2.iter().map(|s| s.as_str()).collect();
            run_drone(&refs2)
        }
    }
}

pub fn drone_go_live() -> Result<Value, String> {
    run_drone(&["go-live", "--workers", "3"])
}

pub fn drone_stats() -> Result<Value, String> {
    run_drone(&["stats"])
}

fn run_drone(sub: &[&str]) -> Result<Value, String> {
    let root = Path::new(DRONE_ROOT);
    if !root.is_dir() {
        return Err(format!("drone root missing: {DRONE_ROOT}"));
    }
    let py = python();
    let mut cmd = Command::new(&py);
    cmd.current_dir(root)
        .env("PYTHONPATH", root)
        .arg("-m")
        .arg("drone");
    for a in sub {
        cmd.arg(a);
    }
    let out = cmd.output().map_err(|e| format!("spawn python: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).to_string();
    if !out.status.success() {
        return Err(format!(
            "drone exit {:?} stderr={} stdout={}",
            out.status.code(),
            &stderr[..stderr.len().min(500)],
            &stdout[..stdout.len().min(500)]
        ));
    }
    parse_json(&stdout)
}

pub fn mount_status() -> Result<Value, String> {
    let exe = Path::new(MOUNT_EXE);
    if !exe.is_file() {
        return Err(format!("mount exe missing: {MOUNT_EXE}"));
    }
    let out = Command::new(exe)
        .arg("status")
        .output()
        .map_err(|e| e.to_string())?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if !out.status.success() {
        return Err(format!("mount status failed: {stdout}"));
    }
    parse_json(&stdout)
}

pub fn mount_all() -> Result<Value, String> {
    let exe = Path::new(MOUNT_EXE);
    if !exe.is_file() {
        return Err(format!("mount exe missing: {MOUNT_EXE}"));
    }
    let out = Command::new(exe)
        .arg("mount")
        .output()
        .map_err(|e| e.to_string())?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if !out.status.success() {
        return Err(format!("mount failed: {stdout}"));
    }
    parse_json(&stdout)
}

pub fn open_path(path: &str) {
    let _ = open::that(path);
}
