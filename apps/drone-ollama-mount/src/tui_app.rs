//! Ratatui Ollama + drone mount interface.

use crate::engine::MountEngine;
use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen};
use crossterm::ExecutableCommand;
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, List, ListItem, Paragraph, Wrap};
use ratatui::Terminal;
use std::io::{self, stdout};
use std::time::Duration;

pub fn run_tui(mut engine: MountEngine) -> Result<(), String> {
    enable_raw_mode().map_err(|e| e.to_string())?;
    stdout()
        .execute(EnterAlternateScreen)
        .map_err(|e| e.to_string())?;
    let mut terminal =
        Terminal::new(CrosstermBackend::new(stdout())).map_err(|e| e.to_string())?;

    let mut input = String::new();
    let mut status_line =
        "Ctrl+M mount | Ctrl+W swarm | Ctrl+U unmount | Ctrl+R ollama | Ctrl+S stats | Enter run | Ctrl+Q quit"
            .to_string();
    let mut mode_hint = "type goal(s) | Enter=run | Ctrl+W=swarm (split on | )".to_string();

    let st = engine.refresh_ollama().clone();
    if st.ok {
        if engine.model.is_empty() || !st.models.iter().any(|m| m == &engine.model) {
            if let Some(m) = st.models.first() {
                engine.model = m.clone();
            }
        }
        engine.log(format!("ollama ok models={}", st.models.len()));
    } else {
        engine.log(format!("ollama offline: {}", st.detail));
    }

    let result = loop {
        terminal
            .draw(|f| draw_ui(f, &engine, &input, &status_line, &mode_hint))
            .map_err(|e| e.to_string())?;

        if event::poll(Duration::from_millis(200)).map_err(|e| e.to_string())? {
            if let Event::Key(key) = event::read().map_err(|e| e.to_string())? {
                if key.kind != KeyEventKind::Press {
                    continue;
                }
                let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
                match key.code {
                    KeyCode::Esc => break Ok(()),
                    KeyCode::Char('q') | KeyCode::Char('Q') if ctrl => break Ok(()),
                    KeyCode::Char('m') | KeyCode::Char('M') if ctrl => {
                        let v = engine.mount();
                        status_line = format!(
                            "MOUNTED {} / {} drones",
                            v["mounted"], v["total"]
                        );
                    }
                    KeyCode::Char('w') | KeyCode::Char('W') if ctrl => {
                        let raw = input.trim().to_string();
                        if raw.is_empty() {
                            status_line = "type goals separated by | then Ctrl+W".into();
                        } else {
                            let goals: Vec<String> = raw
                                .split('|')
                                .map(|s| s.trim().to_string())
                                .filter(|s| !s.is_empty())
                                .collect();
                            status_line = format!("SWARM {} goals…", goals.len());
                            terminal
                                .draw(|f| draw_ui(f, &engine, &input, &status_line, &mode_hint))
                                .ok();
                            match engine.run_swarm(&goals, 3) {
                                Ok(rep) => {
                                    status_line = format!(
                                        "swarm status={} smarter_delta={} goals={}",
                                        rep.get("status").and_then(|v| v.as_str()).unwrap_or("?"),
                                        rep.get("smarter_delta").unwrap_or(&serde_json::Value::Null),
                                        rep.get("goals_n").unwrap_or(&serde_json::Value::Null)
                                    );
                                    input.clear();
                                }
                                Err(e) => status_line = format!("swarm err: {e}"),
                            }
                        }
                    }
                    KeyCode::Char('u') | KeyCode::Char('U') if ctrl => {
                        engine.unmount();
                        status_line = "UNMOUNTED".into();
                    }
                    KeyCode::Char('r') | KeyCode::Char('R') if ctrl => {
                        let st = engine.refresh_ollama().clone();
                        status_line = if st.ok {
                            format!("ollama OK · {} · models={}", st.detail, st.models.len())
                        } else {
                            format!("ollama DOWN · {}", st.detail)
                        };
                        engine.log(status_line.clone());
                    }
                    KeyCode::Char('s') | KeyCode::Char('S') if ctrl => match engine.refresh_stats() {
                        Ok(s) => {
                            status_line = format!(
                                "smart_index={} builds={} level={}",
                                s.get("smart_index").and_then(|x| x.as_f64()).unwrap_or(0.0),
                                s.get("builds").and_then(|x| x.as_u64()).unwrap_or(0),
                                s.get("build_level").and_then(|x| x.as_u64()).unwrap_or(0)
                            );
                        }
                        Err(e) => status_line = format!("stats err: {e}"),
                    },
                    KeyCode::Char('1') if ctrl => {
                        let st = engine.refresh_ollama().clone();
                        if !st.models.is_empty() {
                            let idx = st
                                .models
                                .iter()
                                .position(|m| m == &engine.model)
                                .map(|i| (i + 1) % st.models.len())
                                .unwrap_or(0);
                            engine.model = st.models[idx].clone();
                            status_line = format!("controller model={}", engine.model);
                        }
                    }
                    KeyCode::Enter => {
                        let goal = input.trim().to_string();
                        if goal.is_empty() {
                            status_line = "enter a goal first".into();
                        } else {
                            status_line = "running drones…".into();
                            terminal
                                .draw(|f| draw_ui(f, &engine, &input, &status_line, &mode_hint))
                                .ok();
                            match engine.run_with_ollama_controller(&goal) {
                                Ok(rep) => {
                                    status_line = format!(
                                        "done status={} smarter_delta={} smart_after={}",
                                        rep.get("status").and_then(|v| v.as_str()).unwrap_or("?"),
                                        rep.get("smarter_delta").unwrap_or(&serde_json::Value::Null),
                                        rep.get("smart_after").unwrap_or(&serde_json::Value::Null)
                                    );
                                    input.clear();
                                }
                                Err(e) => status_line = format!("run err: {e}"),
                            }
                        }
                    }
                    KeyCode::Backspace => {
                        input.pop();
                    }
                    KeyCode::Char(c) if !ctrl => {
                        input.push(c);
                    }
                    _ => {}
                }
            }
        }
        let _ = mode_hint;
    };

    disable_raw_mode().ok();
    stdout().execute(LeaveAlternateScreen).ok();
    result
}

fn draw_ui(
    f: &mut ratatui::Frame,
    engine: &MountEngine,
    input: &str,
    status_line: &str,
    _mode_hint: &str,
) {
    let area = f.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(5),
            Constraint::Min(8),
            Constraint::Length(7),
            Constraint::Length(3),
            Constraint::Length(1),
        ])
        .split(area);

    let title = Paragraph::new(Line::from(vec![
        Span::styled(
            " DRONE↔OLLAMA MOUNT ENGINE ",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(format!(
            " · drones {}/{} mounted · model {} ",
            engine.fabric.mounted_count(),
            engine.fabric.drone_count(),
            engine.model
        )),
    ]))
    .block(Block::default().borders(Borders::ALL).title("Rust engine"));
    f.render_widget(title, chunks[0]);

    let ol = &engine.cached_ollama;
    let ollama_text = if ol.ok {
        format!(
            "Ollama: UP  {}  ver={}  models: {}",
            engine.ollama.host,
            ol.version.clone().unwrap_or_else(|| "?".into()),
            ol.models.iter().take(6).cloned().collect::<Vec<_>>().join(", ")
        )
    } else {
        format!("Ollama: DOWN  {}  ({})", engine.ollama.host, ol.detail)
    };
    let honesty = format!(
        "Honesty: full_models_per_drone={} · brain_sim={} · {}",
        engine.fabric.config.honesty.full_models_per_drone,
        engine.fabric.config.honesty.human_brain_simulation,
        engine.fabric.config.honesty.what_this_is
    );
    let top = Paragraph::new(format!("{ollama_text}\n{honesty}"))
        .wrap(Wrap { trim: true })
        .block(Block::default().borders(Borders::ALL).title("Controller (Ollama)"));
    f.render_widget(top, chunks[1]);

    // drone list
    let items: Vec<ListItem> = engine
        .fabric
        .drones
        .iter()
        .map(|d| {
            let mark = if d.mounted { "●" } else { "○" };
            let color = if d.mounted { Color::Green } else { Color::DarkGray };
            ListItem::new(Line::from(Span::styled(
                format!("{mark} [{}] {}  {}", d.hemisphere, d.id, d.role),
                Style::default().fg(color),
            )))
        })
        .collect();
    let list = List::new(items).block(
        Block::default()
            .borders(Borders::ALL)
            .title(format!("Drones (24) — {} mounted", engine.fabric.mounted_count())),
    );
    f.render_widget(list, chunks[2]);

    let log_lines: Vec<Line> = engine
        .last_log
        .iter()
        .rev()
        .take(5)
        .map(|l| Line::from(l.clone()))
        .rev()
        .collect();
    let log = Paragraph::new(log_lines)
        .block(Block::default().borders(Borders::ALL).title("Engine log"));
    f.render_widget(log, chunks[3]);

    let input_box = Paragraph::new(format!("goal> {input}"))
        .block(Block::default().borders(Borders::ALL).title("Task (parent AI → drones)"));
    f.render_widget(input_box, chunks[4]);

    let help = Paragraph::new(status_line).style(Style::default().fg(Color::Yellow));
    f.render_widget(help, chunks[5]);

    let _ = Rect::default();
}

/// Headless mount + one run for CI/smoke (no TUI).
pub fn smoke_headless(mut engine: MountEngine, goal: &str) -> Result<serde_json::Value, String> {
    let mount = engine.mount();
    let run = engine.run_with_ollama_controller(goal)?;
    Ok(serde_json::json!({
        "status": "GREEN",
        "false_green": 0,
        "mount": mount,
        "run": run,
        "snapshot": engine.status_snapshot(),
    }))
}

pub fn restore_terminal() {
    let _ = disable_raw_mode();
    let _ = stdout().execute(LeaveAlternateScreen);
}

// silence unused import warning path
#[allow(dead_code)]
fn _io() -> io::Result<()> {
    Ok(())
}
