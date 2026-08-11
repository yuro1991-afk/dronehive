//! Mount engine: register controllable drones with Ollama controller session.

use crate::fabric::{default_drone_root, FabricMount};
use crate::ollama::{OllamaClient, OllamaStatus};
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone)]
pub struct MountEngine {
    pub fabric: FabricMount,
    pub ollama: OllamaClient,
    pub model: String,
    pub out_dir: PathBuf,
    pub last_log: Vec<String>,
    pub cached_ollama: OllamaStatus,
}

impl MountEngine {
    pub fn open(drone_root: Option<PathBuf>, ollama_host: &str, model: &str) -> Result<Self, String> {
        let root = drone_root.unwrap_or_else(default_drone_root);
        let mut fabric = FabricMount::discover(root)?;
        // default: not mounted until explicit mount (honest)
        fabric.unmount_all();
        let out_dir = PathBuf::from(r"G:\AI-Home\projects\drone-ollama-mount\out");
        fs::create_dir_all(&out_dir).ok();
        let ollama = OllamaClient::new(ollama_host);
        let cached_ollama = ollama.status();
        Ok(Self {
            fabric,
            ollama,
            model: model.to_string(),
            out_dir,
            last_log: vec!["engine open".into()],
            cached_ollama,
        })
    }

    pub fn refresh_ollama(&mut self) -> &OllamaStatus {
        self.cached_ollama = self.ollama.status();
        &self.cached_ollama
    }

    pub fn log(&mut self, line: impl Into<String>) {
        let ts = chrono::Local::now().format("%H:%M:%S");
        self.last_log.push(format!("[{ts}] {}", line.into()));
        if self.last_log.len() > 200 {
            let drain = self.last_log.len() - 200;
            self.last_log.drain(0..drain);
        }
    }

    pub fn mount(&mut self) -> Value {
        self.fabric.mount_all();
        let n = self.fabric.mounted_count();
        self.log(format!("MOUNTED {n} drones onto Ollama session (controller model={})", self.model));
        let seal = json!({
            "event": "mount",
            "mounted": n,
            "total": self.fabric.drone_count(),
            "full_models_per_drone": false,
            "controller": "ollama",
            "model": self.model,
            "drone_root": self.fabric.root.display().to_string(),
            "false_green": 0,
        });
        self.write_seal("last_mount.json", &seal);
        seal
    }

    pub fn unmount(&mut self) -> Value {
        self.fabric.unmount_all();
        self.log("UNMOUNTED all drones");
        json!({"event":"unmount","mounted":0})
    }

    pub fn status_snapshot(&self) -> Value {
        let ol = self.ollama.status();
        json!({
            "ollama_ok": ol.ok,
            "ollama_detail": ol.detail,
            "ollama_version": ol.version,
            "models": ol.models,
            "controller_model": self.model,
            "drones_total": self.fabric.drone_count(),
            "drones_mounted": self.fabric.mounted_count(),
            "full_models_per_drone": self.fabric.config.honesty.full_models_per_drone,
            "human_brain_simulation": self.fabric.config.honesty.human_brain_simulation,
            "drone_root": self.fabric.root.display().to_string(),
            "left": self.fabric.config.hemispheres.left.nodes.len(),
            "right": self.fabric.config.hemispheres.right.nodes.len(),
        })
    }

    /// Swarm fan-out: multiple goals in parallel via mounted drones.
    pub fn run_swarm(
        &mut self,
        goals: &[String],
        workers: u32,
    ) -> Result<Value, String> {
        if self.fabric.mounted_count() == 0 {
            return Err("mount drones first".into());
        }
        let controller = if self.cached_ollama.ok {
            "ollama"
        } else {
            "local"
        };
        self.log(format!(
            "SWARM start goals={} workers={} controller={}",
            goals.len(),
            workers,
            controller
        ));
        let report = self.fabric.run_swarm(goals, controller, workers)?;
        self.log(format!(
            "SWARM done status={} smarter_delta={}",
            report.get("status").and_then(|v| v.as_str()).unwrap_or("?"),
            report.get("smarter_delta").unwrap_or(&Value::Null)
        ));
        self.write_seal("last_swarm.json", &report);
        Ok(report)
    }

    /// Ollama (parent) plans, then mounted drones execute + learn.
    pub fn run_with_ollama_controller(&mut self, goal: &str) -> Result<Value, String> {
        if self.fabric.mounted_count() == 0 {
            return Err("mount drones first (m)".into());
        }
        let ol = self.ollama.status();
        if !ol.ok {
            // still allow local controller path via python --controller local
            self.log(format!("ollama down ({}); falling back controller=local", ol.detail));
            let report = self.fabric.run_task(goal, "local")?;
            self.log(format!(
                "run local ok smart_after={}",
                report.get("smart_after").unwrap_or(&Value::Null)
            ));
            self.write_seal("last_run.json", &report);
            return Ok(report);
        }

        // Parent AI brief (optional assist) — drones still run via fabric
        let brief = match self.ollama.controller_plan(&self.model, goal) {
            Ok(b) if !b.is_empty() => {
                self.log(format!("ollama brief: {}", truncate(&b, 120)));
                b
            }
            Ok(_) => goal.to_string(),
            Err(e) => {
                self.log(format!("ollama brief failed ({e}); using raw goal"));
                goal.to_string()
            }
        };

        let composed = if brief == goal {
            goal.to_string()
        } else {
            format!("{goal} | commander_brief: {brief}")
        };

        let report = self.fabric.run_task(&composed, "ollama")?;
        self.log(format!(
            "run ollama-controller status={} smarter_delta={}",
            report.get("status").and_then(|v| v.as_str()).unwrap_or("?"),
            report.get("smarter_delta").unwrap_or(&Value::Null)
        ));
        self.write_seal("last_run.json", &report);
        Ok(report)
    }

    pub fn refresh_stats(&mut self) -> Result<Value, String> {
        let s = self.fabric.stats()?;
        self.log(format!(
            "stats smart_index={} builds={}",
            s.get("smart_index").unwrap_or(&Value::Null),
            s.get("builds").unwrap_or(&Value::Null)
        ));
        Ok(s)
    }

    fn write_seal(&self, name: &str, v: &Value) {
        let path = self.out_dir.join(name);
        let _ = fs::write(path, serde_json::to_string_pretty(v).unwrap_or_default());
        // also timestamped
        let epoch = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let path2 = self.out_dir.join(format!("{epoch}_{name}"));
        let _ = fs::write(path2, serde_json::to_string_pretty(v).unwrap_or_default());
    }
}

fn truncate(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_string()
    } else {
        format!("{}…", s.chars().take(n).collect::<String>())
    }
}
