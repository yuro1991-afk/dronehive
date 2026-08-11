//! DroneHive Pro — lean Rust TUI command console.
//!
//! Looks like a Grok Build command strip:
//!   - input bar (primary)
//!   - tiny progress
//!   - one status line
//!
//! Work = Python Pro agent under the hood (`python -m drone app pro`).
//! UI stays thin so the machine focuses on the task.

use clap::Parser;
use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use crossterm::ExecutableCommand;
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Gauge, Paragraph};
use ratatui::Terminal;
use serde_json::Value;
use std::io::{self, stdout};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::{Duration, Instant};

#[derive(Parser, Debug)]
#[command(name = "dronehive-tui", about = "DroneHive Pro lean command console")]
struct Args {
    /// Project root (defaults to DRONE_HIVE_ROOT or parent of apps/)
    #[arg(long)]
    root: Option<PathBuf>,

    /// Python executable
    #[arg(long)]
    python: Option<PathBuf>,

    /// Also fire hive after pro agent
    #[arg(long)]
    hive: bool,
}

enum JobMsg {
    Done {
        ok: bool,
        status: String,
        tools: u64,
        seal: String,
        err: Option<String>,
    },
}

struct App {
    root: PathBuf,
    python: PathBuf,
    hive: bool,
    input: String,
    status: String,
    progress: f64,
    busy: bool,
    last_ok: Option<bool>,
    tick: u64,
    rx: Option<Receiver<JobMsg>>,
}

impl App {
    fn new(root: PathBuf, python: PathBuf, hive: bool) -> Self {
        Self {
            root,
            python,
            hive,
            input: String::new(),
            status: "ready · type task · Enter run · Esc quit".into(),
            progress: 0.0,
            busy: false,
            last_ok: None,
            tick: 0,
            rx: None,
        }
    }

    fn submit(&mut self) {
        if self.busy {
            self.status = "busy".into();
            return;
        }
        let goal = self.input.trim().to_string();
        if goal.is_empty() {
            self.status = "type a task".into();
            return;
        }

        let (tx, rx) = mpsc::channel();
        self.rx = Some(rx);
        self.busy = true;
        self.last_ok = None;
        self.progress = 0.08;
        self.status = format!("working… {goal}");
        // keep input until success so user can re-run; clear on GREEN

        let root = self.root.clone();
        let py = self.python.clone();
        let hive = self.hive;
        let g = goal.clone();

        thread::spawn(move || {
            let msg = run_pro_job(&py, &root, &g, hive);
            let _ = tx.send(msg);
        });
    }

    fn poll_job(&mut self) {
        let Some(rx) = self.rx.as_ref() else {
            return;
        };
        match rx.try_recv() {
            Ok(JobMsg::Done {
                ok,
                status,
                tools,
                seal,
                err,
            }) => {
                self.busy = false;
                self.last_ok = Some(ok);
                self.progress = if ok { 1.0 } else { 0.12 };
                if let Some(e) = err {
                    self.status = format!("RED · {e}");
                } else {
                    let seal_name = Path::new(&seal)
                        .file_name()
                        .map(|s| s.to_string_lossy().into_owned())
                        .unwrap_or_else(|| seal.clone());
                    self.status = format!("{status} · tools={tools} · {seal_name}");
                    if ok {
                        self.input.clear();
                    }
                }
                self.rx = None;
            }
            Err(mpsc::TryRecvError::Empty) => {}
            Err(mpsc::TryRecvError::Disconnected) => {
                self.busy = false;
                self.rx = None;
                self.status = "worker dropped".into();
            }
        }
    }

    fn tick_progress(&mut self) {
        self.tick = self.tick.wrapping_add(1);
        if self.busy {
            self.progress += 0.04;
            if self.progress >= 0.92 {
                self.progress = 0.08;
            }
        }
    }
}

fn run_pro_job(python: &Path, root: &Path, goal: &str, hive: bool) -> JobMsg {
    let mut cmd = Command::new(python);
    cmd.current_dir(root)
        .env("DRONE_HIVE_ROOT", root)
        .env("PYTHONUTF8", "1")
        .args(["-u", "-m", "drone", "app", "pro", "--goal", goal, "--rounds", "6"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if hive {
        cmd.arg("--hive");
    }

    match cmd.output() {
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            let stderr = String::from_utf8_lossy(&out.stderr);
            // parse last JSON object from stdout
            if let Some(v) = parse_json_blob(&stdout) {
                let status = v
                    .get("status")
                    .and_then(|x| x.as_str())
                    .unwrap_or("?")
                    .to_string();
                let tools = v
                    .get("tool_calls")
                    .and_then(|x| x.as_u64())
                    .or_else(|| {
                        v.get("agent")
                            .and_then(|a| a.get("tool_calls"))
                            .and_then(|x| x.as_u64())
                    })
                    .unwrap_or(0);
                let seal = v
                    .get("seal_path")
                    .and_then(|x| x.as_str())
                    .or_else(|| v.get("report_path").and_then(|x| x.as_str()))
                    .unwrap_or("")
                    .to_string();
                let ok = status.eq_ignore_ascii_case("GREEN") || out.status.success();
                JobMsg::Done {
                    ok,
                    status,
                    tools,
                    seal,
                    err: None,
                }
            } else {
                let err = if !stderr.trim().is_empty() {
                    stderr.chars().take(180).collect()
                } else if !stdout.trim().is_empty() {
                    stdout.chars().take(180).collect()
                } else {
                    format!("exit {:?}", out.status.code())
                };
                JobMsg::Done {
                    ok: false,
                    status: "RED".into(),
                    tools: 0,
                    seal: String::new(),
                    err: Some(err),
                }
            }
        }
        Err(e) => JobMsg::Done {
            ok: false,
            status: "RED".into(),
            tools: 0,
            seal: String::new(),
            err: Some(format!("spawn: {e}")),
        },
    }
}

fn parse_json_blob(s: &str) -> Option<Value> {
    // try whole string, then last {...}
    if let Ok(v) = serde_json::from_str::<Value>(s.trim()) {
        return Some(v);
    }
    let bytes = s.as_bytes();
    let mut depth = 0i32;
    let mut end = None;
    let mut start = None;
    for (i, &b) in bytes.iter().enumerate().rev() {
        match b {
            b'}' => {
                if depth == 0 {
                    end = Some(i);
                }
                depth += 1;
            }
            b'{' => {
                depth -= 1;
                if depth == 0 {
                    start = Some(i);
                    break;
                }
            }
            _ => {}
        }
    }
    match (start, end) {
        (Some(a), Some(b)) if a < b => serde_json::from_str(&s[a..=b]).ok(),
        _ => None,
    }
}

fn default_python() -> PathBuf {
    if let Ok(p) = std::env::var("DRONE_PYTHON") {
        return PathBuf::from(p);
    }
    if let Ok(la) = std::env::var("LOCALAPPDATA") {
        let p = PathBuf::from(la)
            .join("Programs")
            .join("Python")
            .join("Python312")
            .join("python.exe");
        if p.is_file() {
            return p;
        }
    }
    PathBuf::from("python")
}

fn default_root() -> PathBuf {
    if let Ok(r) = std::env::var("DRONE_HIVE_ROOT") {
        let p = PathBuf::from(r);
        if p.is_dir() {
            return p;
        }
    }
    // apps/dronehive-tui -> ../../
    let here = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let cand = here.join("../..");
    if cand.join("drone").is_dir() {
        return cand.canonicalize().unwrap_or(cand);
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn draw(f: &mut ratatui::Frame, app: &App) {
    let area = f.area();
    // lean vertical: title | status | gauge | input
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Min(3),
        ])
        .split(area);

    let title = Paragraph::new(Line::from(vec![
        Span::styled(
            " DroneHive ",
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled("Pro ", Style::default().fg(Color::Rgb(251, 191, 36))),
        Span::styled("· console", Style::default().fg(Color::DarkGray)),
        Span::raw("  "),
        Span::styled(
            if app.busy { "● RUN" } else { "○ idle" },
            Style::default().fg(if app.busy {
                Color::Green
            } else {
                Color::DarkGray
            }),
        ),
    ]));
    f.render_widget(title, chunks[0]);

    let st_color = match app.last_ok {
        Some(true) => Color::Green,
        Some(false) => Color::Red,
        None if app.busy => Color::Yellow,
        None => Color::Gray,
    };
    let status = Paragraph::new(Line::from(Span::styled(
        format!(" {}", app.status),
        Style::default().fg(st_color),
    )));
    f.render_widget(status, chunks[1]);

    let ratio = app.progress.clamp(0.0, 1.0);
    let gauge = Gauge::default()
        .gauge_style(Style::default().fg(if app.busy {
            Color::Yellow
        } else if app.last_ok == Some(true) {
            Color::Green
        } else if app.last_ok == Some(false) {
            Color::Red
        } else {
            Color::DarkGray
        }))
        .ratio(ratio)
        .label("");
    f.render_widget(gauge, chunks[2]);

    let input_block = Block::default()
        .borders(Borders::TOP)
        .border_style(Style::default().fg(Color::DarkGray))
        .title(Span::styled(
            " › ",
            Style::default().fg(Color::Yellow),
        ));
    let cursor = if app.tick % 2 == 0 && !app.busy {
        "▌"
    } else {
        " "
    };
    let shown = if app.busy && app.input.is_empty() {
        "(running…)"
    } else {
        app.input.as_str()
    };
    let input = Paragraph::new(format!(" {shown}{cursor}")).block(input_block).style(
        Style::default().fg(if app.busy {
            Color::DarkGray
        } else {
            Color::White
        }),
    );
    f.render_widget(input, chunks[3]);
}

fn run_tui(mut app: App) -> io::Result<()> {
    enable_raw_mode()?;
    stdout().execute(EnterAlternateScreen)?;
    let mut terminal = Terminal::new(CrosstermBackend::new(stdout()))?;

    let mut last_tick = Instant::now();
    let tick_rate = Duration::from_millis(100);

    let result = loop {
        terminal.draw(|f| draw(f, &app))?;

        let timeout = tick_rate
            .checked_sub(last_tick.elapsed())
            .unwrap_or(Duration::from_millis(0));

        if event::poll(timeout)? {
            if let Event::Key(key) = event::read()? {
                if key.kind != KeyEventKind::Press {
                    continue;
                }
                let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
                match key.code {
                    KeyCode::Esc => break Ok(()),
                    KeyCode::Char('c') | KeyCode::Char('C') if ctrl => break Ok(()),
                    KeyCode::Char('q') | KeyCode::Char('Q') if ctrl => break Ok(()),
                    KeyCode::Enter if !app.busy => {
                        app.submit();
                    }
                    KeyCode::Backspace if !app.busy => {
                        app.input.pop();
                    }
                    KeyCode::Char(c) if !app.busy && !ctrl => {
                        // printable
                        if !c.is_control() {
                            app.input.push(c);
                        }
                    }
                    _ => {}
                }
            }
        }

        if last_tick.elapsed() >= tick_rate {
            app.poll_job();
            app.tick_progress();
            last_tick = Instant::now();
        }
    };

    disable_raw_mode()?;
    stdout().execute(LeaveAlternateScreen)?;
    result
}

fn main() {
    let args = Args::parse();
    let root = args.root.unwrap_or_else(default_root);
    let python = args.python.unwrap_or_else(default_python);

    if !root.join("drone").is_dir() {
        eprintln!(
            "HALT: drone package not found under {}",
            root.display()
        );
        eprintln!("Set --root or DRONE_HIVE_ROOT");
        std::process::exit(2);
    }

    let app = App::new(root, python, args.hive);
    if let Err(e) = run_tui(app) {
        eprintln!("tui error: {e}");
        std::process::exit(1);
    }
}
