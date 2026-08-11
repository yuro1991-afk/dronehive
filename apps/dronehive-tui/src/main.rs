//! DroneHive Pro — Grok-style chat console + lean input.
//!
//! Layout (like Grok Build):
//!   chat transcript (drone / tool / system)
//!   tiny progress
//!   input bar
//!
//! Python Pro agent streams `CHAT|role|text` lines; final `SEAL|{json}`.

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
use ratatui::widgets::{Block, Borders, Gauge, List, ListItem, Paragraph};
use ratatui::Terminal;
use serde_json::Value;
use std::io::{self, BufRead, BufReader, stdout, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::{Duration, Instant};

#[derive(Parser, Debug)]
#[command(name = "dronehive-tui", about = "DroneHive Pro chat console")]
struct Args {
    #[arg(long)]
    root: Option<PathBuf>,
    #[arg(long)]
    python: Option<PathBuf>,
    #[arg(long)]
    hive: bool,
}

#[derive(Clone)]
struct ChatLine {
    role: String,
    text: String,
}

enum JobMsg {
    Chat { role: String, text: String },
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
    chat: Vec<ChatLine>,
    scroll: usize,
    rx: Option<Receiver<JobMsg>>,
}

impl App {
    fn new(root: PathBuf, python: PathBuf, hive: bool) -> Self {
        let mut app = Self {
            root,
            python: python.clone(),
            hive,
            input: String::new(),
            status: "ready · type task · Enter run · Esc quit".into(),
            progress: 0.0,
            busy: false,
            last_ok: None,
            tick: 0,
            chat: Vec::new(),
            scroll: 0,
            rx: None,
        };
        app.push_chat(
            "system",
            format!(
                "DroneHive Pro · py={} · chat = drone output",
                python
                    .file_name()
                    .and_then(|s| s.to_str())
                    .unwrap_or("python")
            ),
        );
        app
    }

    fn push_chat(&mut self, role: &str, text: impl Into<String>) {
        self.chat.push(ChatLine {
            role: role.to_string(),
            text: text.into(),
        });
        // keep last 400 lines
        if self.chat.len() > 400 {
            let n = self.chat.len() - 400;
            self.chat.drain(0..n);
        }
        self.scroll = 0; // stick to bottom
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
        self.push_chat("user", goal.clone());
        self.push_chat("system", "dispatching drones…");

        let root = self.root.clone();
        let py = self.python.clone();
        let hive = self.hive;
        let g = goal.clone();

        thread::spawn(move || {
            run_pro_job_stream(&py, &root, &g, hive, tx);
        });
    }

    fn poll_job(&mut self) {
        // Take messages without holding borrow across self mutation
        let mut batch: Vec<JobMsg> = Vec::new();
        if let Some(rx) = self.rx.as_ref() {
            while let Ok(m) = rx.try_recv() {
                batch.push(m);
            }
        }
        let mut done = false;
        let mut dropped = false;
        if self.rx.is_some() && batch.is_empty() {
            // check disconnect without consuming (try_recv already empty)
            // leave as-is
        }
        for msg in batch {
            match msg {
                JobMsg::Chat { role, text } => {
                    self.push_chat(&role, text);
                }
                JobMsg::Done {
                    ok,
                    status,
                    tools,
                    seal,
                    err,
                } => {
                    self.busy = false;
                    self.last_ok = Some(ok);
                    self.progress = if ok { 1.0 } else { 0.12 };
                    if let Some(e) = err {
                        self.status = format!("RED · {e}");
                        self.push_chat("system", format!("failed · {e}"));
                    } else {
                        let seal_name = Path::new(&seal)
                            .file_name()
                            .map(|s| s.to_string_lossy().into_owned())
                            .unwrap_or_else(|| seal.clone());
                        self.status = format!("{status} · tools={tools} · {seal_name}");
                        self.push_chat(
                            "system",
                            format!("done · {status} · tools={tools} · {seal_name}"),
                        );
                        if ok {
                            self.input.clear();
                        }
                    }
                    done = true;
                }
            }
        }
        // detect disconnect if channel gone while busy and no messages
        if self.busy {
            if let Some(rx) = &self.rx {
                if matches!(rx.try_recv(), Err(mpsc::TryRecvError::Disconnected)) {
                    dropped = true;
                }
            }
        }
        if done || dropped {
            if dropped {
                self.busy = false;
                self.status = "worker dropped".into();
                self.push_chat("system", "worker dropped");
            }
            self.rx = None;
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

fn write_last_error(root: &Path, msg: &str) {
    let _ = std::fs::create_dir_all(root.join("out"));
    let _ = std::fs::write(root.join("out").join("TUI_LAST_ERROR.txt"), msg);
}

fn run_pro_job_stream(
    python: &Path,
    root: &Path,
    goal: &str,
    hive: bool,
    tx: mpsc::Sender<JobMsg>,
) {
    let mut cmd = Command::new(python);
    let mut args: Vec<String> = Vec::new();
    let py_name = python
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if py_name == "py.exe" || py_name == "py" {
        args.push("-3".into());
    }
    args.extend([
        "-u".into(),
        "-m".into(),
        "drone".into(),
        "app".into(),
        "pro".into(),
        "--goal".into(),
        goal.to_string(),
        "--rounds".into(),
        "6".into(),
    ]);
    if hive {
        args.push("--hive".into());
    }

    cmd.current_dir(root)
        .env("DRONE_HIVE_ROOT", root)
        .env("PYTHONUTF8", "1")
        .env("PYTHONPATH", root)
        .env("PYTHONUNBUFFERED", "1")
        .args(&args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            let err = format!("spawn failed: {e} · python={}", python.display());
            write_last_error(root, &err);
            let _ = tx.send(JobMsg::Done {
                ok: false,
                status: "RED".into(),
                tools: 0,
                seal: String::new(),
                err: Some(err),
            });
            return;
        }
    };

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();

    // stderr reader
    let tx_err = tx.clone();
    let err_thread = thread::spawn(move || {
        if let Some(err) = stderr {
            let reader = BufReader::new(err);
            for line in reader.lines().flatten() {
                let t = line.trim();
                if t.is_empty() {
                    continue;
                }
                let _ = tx_err.send(JobMsg::Chat {
                    role: "system".into(),
                    text: format!("stderr: {t}"),
                });
            }
        }
    });

    let mut seal_json: Option<Value> = None;
    let mut stdout_tail = String::new();

    if let Some(out) = stdout {
        let reader = BufReader::new(out);
        for line in reader.lines().flatten() {
            stdout_tail.push_str(&line);
            stdout_tail.push('\n');
            if let Some(rest) = line.strip_prefix("CHAT|") {
                let mut parts = rest.splitn(2, '|');
                let role = parts.next().unwrap_or("drone").to_string();
                let text = parts.next().unwrap_or("").to_string();
                let _ = tx.send(JobMsg::Chat { role, text });
            } else if let Some(rest) = line.strip_prefix("SEAL|") {
                if let Ok(v) = serde_json::from_str::<Value>(rest) {
                    seal_json = Some(v);
                }
            } else if line.trim().starts_with('{') {
                // fallback: full json line
                if let Ok(v) = serde_json::from_str::<Value>(line.trim()) {
                    seal_json = Some(v);
                }
            }
        }
    }

    let _ = err_thread.join();
    let status_code = child.wait().ok().and_then(|s| s.code());

    if let Some(v) = seal_json.or_else(|| parse_json_blob(&stdout_tail)) {
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
        let ok = status.eq_ignore_ascii_case("GREEN");
        // surface evidence paths in chat
        if let Some(arr) = v.get("evidence").and_then(|e| e.as_array()) {
            for p in arr.iter().take(8) {
                if let Some(s) = p.as_str() {
                    let _ = tx.send(JobMsg::Chat {
                        role: "tool".into(),
                        text: format!("evidence · {s}"),
                    });
                }
            }
        }
        if !ok {
            write_last_error(
                root,
                &format!("status={status}\nexit={status_code:?}\n"),
            );
        }
        let _ = tx.send(JobMsg::Done {
            ok,
            status: status.clone(),
            tools,
            seal,
            err: if ok {
                None
            } else {
                Some(format!(
                    "{status} · tools={tools} · see out\\TUI_LAST_ERROR.txt"
                ))
            },
        });
    } else {
        let err = format!(
            "no SEAL · py={} · exit={status_code:?}",
            python.display()
        );
        write_last_error(root, &format!("{err}\n{stdout_tail}"));
        let _ = tx.send(JobMsg::Done {
            ok: false,
            status: "RED".into(),
            tools: 0,
            seal: String::new(),
            err: Some(err),
        });
    }
}

fn parse_json_blob(s: &str) -> Option<Value> {
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
        let pb = PathBuf::from(&p);
        if pb.is_file() {
            return pb;
        }
    }
    if let Ok(la) = std::env::var("LOCALAPPDATA") {
        for rel in [
            r"Programs\Python\Python312\python.exe",
            r"Programs\Python\Python311\python.exe",
            r"Programs\Python\Python313\python.exe",
            r"Programs\Python\Python310\python.exe",
        ] {
            let p = PathBuf::from(&la).join(rel);
            if p.is_file() {
                return p;
            }
        }
    }
    for cand in [r"C:\Python312\python.exe", r"C:\Python311\python.exe"] {
        let p = PathBuf::from(cand);
        if p.is_file() {
            return p;
        }
    }
    if let Ok(out) = Command::new("where").arg("py").output() {
        if out.status.success() {
            let s = String::from_utf8_lossy(&out.stdout);
            if let Some(line) = s.lines().next() {
                let p = PathBuf::from(line.trim());
                if p.is_file() {
                    return p;
                }
            }
        }
    }
    if let Ok(out) = Command::new("where").arg("python").output() {
        if out.status.success() {
            let s = String::from_utf8_lossy(&out.stdout);
            for line in s.lines() {
                let p = PathBuf::from(line.trim());
                if p.is_file() && !p.to_string_lossy().contains("WindowsApps") {
                    return p;
                }
            }
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
    let here = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let cand = here.join("../..");
    if cand.join("drone").is_dir() {
        return cand.canonicalize().unwrap_or(cand);
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn role_style(role: &str) -> Style {
    match role {
        "user" => Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD),
        "drone" => Style::default().fg(Color::Yellow),
        "tool" => Style::default().fg(Color::Green),
        "system" => Style::default().fg(Color::DarkGray),
        _ => Style::default().fg(Color::Gray),
    }
}

fn role_label(role: &str) -> String {
    match role {
        "user" => "you".into(),
        "drone" => "drone".into(),
        "tool" => "tool".into(),
        "system" => "sys".into(),
        other => other.to_string(),
    }
}

fn draw(f: &mut ratatui::Frame, app: &App) {
    let area = f.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1), // title
            Constraint::Min(6),    // chat
            Constraint::Length(1), // status
            Constraint::Length(1), // gauge
            Constraint::Length(3), // input
        ])
        .split(area);

    // title
    let title = Paragraph::new(Line::from(vec![
        Span::styled(
            " DroneHive ",
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled("Pro ", Style::default().fg(Color::Rgb(251, 191, 36))),
        Span::styled("· chat", Style::default().fg(Color::DarkGray)),
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

    // chat (Grok-style transcript)
    let h = chunks[1].height.saturating_sub(2) as usize;
    let total = app.chat.len();
    let end = total.saturating_sub(app.scroll);
    let start = end.saturating_sub(h.max(1));
    let items: Vec<ListItem> = app.chat[start..end]
        .iter()
        .map(|c| {
            let label = role_label(&c.role);
            ListItem::new(Line::from(vec![
                Span::styled(format!("{label:>5} "), role_style(&c.role)),
                Span::raw(c.text.clone()),
            ]))
        })
        .collect();
    let chat = List::new(items).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(Color::DarkGray))
            .title(Span::styled(
                " chat ",
                Style::default().fg(Color::DarkGray),
            )),
    );
    f.render_widget(chat, chunks[1]);

    // status
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
    f.render_widget(status, chunks[2]);

    // gauge
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
    f.render_widget(gauge, chunks[3]);

    // input
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
    let input = Paragraph::new(format!(" {shown}{cursor}")).block(
        Block::default()
            .borders(Borders::TOP)
            .border_style(Style::default().fg(Color::DarkGray))
            .title(Span::styled(" › ", Style::default().fg(Color::Yellow))),
    )
    .style(Style::default().fg(if app.busy {
        Color::DarkGray
    } else {
        Color::White
    }));
    f.render_widget(input, chunks[4]);
}

fn run_tui(mut app: App) -> io::Result<()> {
    enable_raw_mode()?;
    stdout().execute(EnterAlternateScreen)?;
    let mut terminal = Terminal::new(CrosstermBackend::new(stdout()))?;

    let mut last_tick = Instant::now();
    let tick_rate = Duration::from_millis(80);

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
                    KeyCode::Enter if !app.busy => app.submit(),
                    KeyCode::Backspace if !app.busy => {
                        app.input.pop();
                    }
                    KeyCode::Up => {
                        app.scroll = app.scroll.saturating_add(1);
                    }
                    KeyCode::Down => {
                        app.scroll = app.scroll.saturating_sub(1);
                    }
                    KeyCode::PageUp => {
                        app.scroll = app.scroll.saturating_add(10);
                    }
                    KeyCode::PageDown => {
                        app.scroll = app.scroll.saturating_sub(10);
                    }
                    KeyCode::Char(c) if !app.busy && !ctrl => {
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
        eprintln!("HALT: drone package not found under {}", root.display());
        eprintln!("Press Enter to close...");
        let _ = io::stdin().read_line(&mut String::new());
        std::process::exit(2);
    }
    if !python.is_file() && python == PathBuf::from("python") {
        eprintln!("HALT: Python not found. Install Python 3.12 or set DRONE_PYTHON.");
        eprintln!("Press Enter to close...");
        let _ = io::stdin().read_line(&mut String::new());
        std::process::exit(2);
    }

    let app = App::new(root.clone(), python, args.hive);
    if let Err(e) = run_tui(app) {
        eprintln!("tui error: {e}");
        write_last_error(&root, &format!("tui error: {e}"));
        eprintln!("Press Enter to close...");
        let _ = io::stdin().read_line(&mut String::new());
        let _ = stdout().flush();
        std::process::exit(1);
    }
}
