//! Load dual-hemisphere drone fabric config (24 controllable drones).

use serde::Deserialize;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Deserialize)]
pub struct BrainFabricConfig {
    pub name: String,
    pub honesty: Honesty,
    pub hemispheres: Hemispheres,
    pub commanders: Commanders,
    pub learning: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Honesty {
    pub full_models_per_drone: bool,
    pub human_brain_simulation: bool,
    pub what_this_is: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Hemispheres {
    pub left: Hemisphere,
    pub right: Hemisphere,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Hemisphere {
    pub id: String,
    pub role: String,
    pub nodes: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Commanders {
    pub allowed: Vec<String>,
    pub human_viewer_chat: bool,
}

#[derive(Debug, Clone)]
pub struct DroneSlot {
    pub id: String,
    pub hemisphere: String,
    pub role: String,
    pub mounted: bool,
}

#[derive(Debug, Clone)]
pub struct FabricMount {
    pub root: PathBuf,
    pub config: BrainFabricConfig,
    pub drones: Vec<DroneSlot>,
    pub python: PathBuf,
}

impl FabricMount {
    pub fn discover(root: impl AsRef<Path>) -> Result<Self, String> {
        let root = root.as_ref().to_path_buf();
        let cfg_path = root.join("configs").join("brain_fabric.json");
        if !cfg_path.exists() {
            return Err(format!("missing fabric config: {}", cfg_path.display()));
        }
        let raw = fs::read_to_string(&cfg_path).map_err(|e| e.to_string())?;
        let config: BrainFabricConfig =
            serde_json::from_str(&raw).map_err(|e| format!("config parse: {e}"))?;

        let mut drones = Vec::new();
        for id in &config.hemispheres.left.nodes {
            drones.push(DroneSlot {
                id: id.clone(),
                hemisphere: "L".into(),
                role: role_of(id),
                mounted: false,
            });
        }
        for id in &config.hemispheres.right.nodes {
            drones.push(DroneSlot {
                id: id.clone(),
                hemisphere: "R".into(),
                role: role_of(id),
                mounted: false,
            });
        }

        let python = find_python();
        Ok(Self {
            root,
            config,
            drones,
            python,
        })
    }

    pub fn drone_count(&self) -> usize {
        self.drones.len()
    }

    pub fn mount_all(&mut self) {
        for d in &mut self.drones {
            d.mounted = true;
        }
    }

    pub fn unmount_all(&mut self) {
        for d in &mut self.drones {
            d.mounted = false;
        }
    }

    pub fn mounted_count(&self) -> usize {
        self.drones.iter().filter(|d| d.mounted).count()
    }

    /// Run fabric task via Python drone CLI (source of learning truth).
    pub fn run_task(&self, goal: &str, controller: &str) -> Result<serde_json::Value, String> {
        self.run_python_drone(
            &["run", "--goal", goal, "--controller", controller],
            true,
        )
    }

    /// Swarm: multi-goal parallel fan-out + L||R hemispheres (Python drone swarm).
    pub fn run_swarm(
        &self,
        goals: &[String],
        controller: &str,
        workers: u32,
    ) -> Result<serde_json::Value, String> {
        if self.mounted_count() == 0 {
            return Err("no drones mounted — mount first".into());
        }
        if goals.is_empty() {
            return Err("swarm needs at least one goal".into());
        }
        let workers_s = workers.max(1).to_string();
        let mut args: Vec<String> = vec![
            "swarm".into(),
            "--controller".into(),
            controller.into(),
            "--workers".into(),
            workers_s,
        ];
        for g in goals {
            args.push("--goal".into());
            args.push(g.clone());
        }
        let arg_refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
        self.run_python_drone(&arg_refs, true)
    }

    fn run_python_drone(&self, subargs: &[&str], require_mounted: bool) -> Result<serde_json::Value, String> {
        if require_mounted && self.mounted_count() == 0 {
            return Err("no drones mounted — press Ctrl+M to mount".into());
        }
        let py = &self.python;
        if !py.exists() {
            return Err(format!("python not found: {}", py.display()));
        }
        let mut cmd_args = vec!["-m", "drone"];
        cmd_args.extend_from_slice(subargs);
        let output = std::process::Command::new(py)
            .current_dir(&self.root)
            .env("PYTHONPATH", &self.root)
            .args(&cmd_args)
            .output()
            .map_err(|e| format!("spawn python: {e}"))?;

        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        if !output.status.success() {
            return Err(format!(
                "drone failed code={:?}\nstderr={}\nstdout={}",
                output.status.code(),
                stderr.chars().take(800).collect::<String>(),
                stdout.chars().take(800).collect::<String>()
            ));
        }
        parse_json_blob(&stdout)
            .or_else(|| serde_json::from_str(&stdout).ok())
            .ok_or_else(|| {
                format!(
                    "could not parse drone JSON:\n{}",
                    &stdout[..stdout.len().min(500)]
                )
            })
    }

    pub fn stats(&self) -> Result<serde_json::Value, String> {
        let py = &self.python;
        let output = std::process::Command::new(py)
            .current_dir(&self.root)
            .env("PYTHONPATH", &self.root)
            .args(["-m", "drone", "stats"])
            .output()
            .map_err(|e| format!("spawn python: {e}"))?;
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        if !output.status.success() {
            return Err(format!("stats failed: {}", String::from_utf8_lossy(&output.stderr)));
        }
        serde_json::from_str(&stdout).map_err(|e| format!("stats parse: {e}"))
    }
}

fn role_of(node_id: &str) -> String {
    node_id
        .split_once('_')
        .map(|(_, r)| r.to_string())
        .unwrap_or_else(|| "generic".into())
}

fn find_python() -> PathBuf {
    let candidates = [
        PathBuf::from(r"C:\Users\yuro1\AppData\Local\Programs\Python\Python312\python.exe"),
        PathBuf::from(r"C:\Python312\python.exe"),
        PathBuf::from(r"C:\Python311\python.exe"),
    ];
    for c in candidates {
        if c.exists() {
            return c;
        }
    }
    PathBuf::from("python")
}

fn parse_json_blob(s: &str) -> Option<serde_json::Value> {
    // find outermost { ... } from first {
    let start = s.find('{')?;
    let slice = &s[start..];
    // try full parse; if fail, try from last complete object
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(slice) {
        return Some(v);
    }
    // balance braces
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
    serde_json::from_str(&slice[..=end]).ok()
}

pub fn default_drone_root() -> PathBuf {
    PathBuf::from(r"G:\AI-Home\projects\ai-worker-drone-0.5b")
}
