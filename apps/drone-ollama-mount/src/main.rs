//! drone-ollama-mount — Rust engine to mount worker drones onto Ollama TUI.

mod engine;
mod fabric;
mod ollama;
mod tui_app;

use clap::{Parser, Subcommand};
use engine::MountEngine;
use fabric::default_drone_root;
use std::path::PathBuf;
use std::process::ExitCode;

#[derive(Parser, Debug)]
#[command(name = "drone-ollama-mount")]
#[command(about = "Mount AI worker drones onto Ollama TUI (Rust engine)")]
struct Cli {
    /// Ollama host
    #[arg(long, default_value = "http://127.0.0.1:11434")]
    host: String,

    /// Controller model on Ollama
    #[arg(long, default_value = "llama3.2:3b")]
    model: String,

    /// Path to ai-worker-drone-0.5b
    #[arg(long)]
    drone_root: Option<PathBuf>,

    #[command(subcommand)]
    cmd: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Interactive ratatui TUI (Ollama + mount drones)
    Tui,
    /// Mount all 24 drones (headless) and write seal
    Mount,
    /// Unmount
    Unmount,
    /// Print JSON status
    Status,
    /// Mount + run one goal via Ollama controller
    Run {
        #[arg(long)]
        goal: String,
    },
    /// Mount + SWARM multi-goal (parallel hemispheres + fan-out)
    Swarm {
        #[arg(long, action = clap::ArgAction::Append)]
        goal: Vec<String>,
        #[arg(long, default_value_t = 3)]
        workers: u32,
    },
    /// Headless smoke: mount + run + seal
    Smoke,
    /// Headless swarm smoke
    SwarmSmoke,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let root = cli.drone_root.or_else(|| Some(default_drone_root()));
    let engine = match MountEngine::open(root, &cli.host, &cli.model) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("HALT: engine open failed: {e}");
            return ExitCode::from(1);
        }
    };

    match cli.cmd {
        Commands::Tui => {
            if let Err(e) = tui_app::run_tui(engine) {
                tui_app::restore_terminal();
                eprintln!("TUI error: {e}");
                return ExitCode::from(1);
            }
            ExitCode::SUCCESS
        }
        Commands::Mount => {
            let mut engine = engine;
            let v = engine.mount();
            println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
            ExitCode::SUCCESS
        }
        Commands::Unmount => {
            let mut engine = engine;
            let v = engine.unmount();
            println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
            ExitCode::SUCCESS
        }
        Commands::Status => {
            let v = engine.status_snapshot();
            println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
            ExitCode::SUCCESS
        }
        Commands::Run { goal } => {
            let mut engine = engine;
            engine.mount();
            match engine.run_with_ollama_controller(&goal) {
                Ok(v) => {
                    println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
                    if v.get("status").and_then(|s| s.as_str()) == Some("GREEN") {
                        ExitCode::SUCCESS
                    } else {
                        ExitCode::from(2)
                    }
                }
                Err(e) => {
                    eprintln!("HALT: {e}");
                    ExitCode::from(1)
                }
            }
        }
        Commands::Swarm { goal, workers } => {
            let mut engine = engine;
            engine.mount();
            let goals = if goal.is_empty() {
                vec!["swarm default: build worker path".into()]
            } else {
                goal
            };
            match engine.run_swarm(&goals, workers) {
                Ok(v) => {
                    println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
                    let st = v.get("status").and_then(|s| s.as_str()).unwrap_or("RED");
                    if st == "GREEN" || st == "PARTIAL" {
                        ExitCode::SUCCESS
                    } else {
                        ExitCode::from(2)
                    }
                }
                Err(e) => {
                    eprintln!("HALT: swarm failed: {e}");
                    ExitCode::from(1)
                }
            }
        }
        Commands::Smoke => match tui_app::smoke_headless(engine, "build mount-engine smoke seal") {
            Ok(v) => {
                let path = PathBuf::from(r"G:\AI-Home\projects\drone-ollama-mount\out\SMOKE_SEAL.json");
                let _ = std::fs::write(
                    &path,
                    serde_json::to_string_pretty(&v).unwrap_or_default(),
                );
                println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
                println!("seal: {}", path.display());
                ExitCode::SUCCESS
            }
            Err(e) => {
                eprintln!("HALT: smoke failed: {e}");
                ExitCode::from(1)
            }
        },
        Commands::SwarmSmoke => {
            let mut engine = engine;
            engine.mount();
            let goals = vec![
                "swarm smoke: intake path".into(),
                "swarm smoke: critic path".into(),
                "swarm smoke: callosum under load".into(),
            ];
            match engine.run_swarm(&goals, 3) {
                Ok(v) => {
                    let path = PathBuf::from(
                        r"G:\AI-Home\projects\drone-ollama-mount\out\SWARM_SMOKE_SEAL.json",
                    );
                    let _ = std::fs::write(
                        &path,
                        serde_json::to_string_pretty(&v).unwrap_or_default(),
                    );
                    println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
                    println!("seal: {}", path.display());
                    let st = v.get("status").and_then(|s| s.as_str()).unwrap_or("RED");
                    if st == "GREEN" || st == "PARTIAL" {
                        ExitCode::SUCCESS
                    } else {
                        ExitCode::from(2)
                    }
                }
                Err(e) => {
                    eprintln!("HALT: swarm-smoke failed: {e}");
                    ExitCode::from(1)
                }
            }
        },
    }
}
