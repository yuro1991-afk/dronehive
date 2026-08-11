//! Drone Ollama App — top-tier installed workbench.
//! Native egui · Ollama wired · 24 worker drones · mount · swarm · go-live.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod drone_bridge;
mod ollama_wire;

use eframe::egui;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver, Sender, TryRecvError};
use std::thread;
use std::time::{Duration, Instant};

fn crash_log_path() -> PathBuf {
    PathBuf::from(r"G:\AI-Home\apps\DroneOllama\logs\crash.log")
}

fn write_crash(msg: &str) {
    let path = crash_log_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(
            f,
            "[{}] {}",
            chrono::Local::now().format("%Y-%m-%dT%H:%M:%S"),
            msg
        );
    }
}

/// Single-instance: if another copy is open, exit quietly (do not kill the other).
fn already_running() -> bool {
    use std::sync::atomic::{AtomicBool, Ordering};
    static HELD: AtomicBool = AtomicBool::new(false);
    // Named mutex via Win32
    #[cfg(windows)]
    {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;
        #[link(name = "kernel32")]
        extern "system" {
            fn CreateMutexW(
                lpMutexAttributes: *const core::ffi::c_void,
                bInitialOwner: i32,
                lpName: *const u16,
            ) -> *mut core::ffi::c_void;
            fn GetLastError() -> u32;
        }
        const ERROR_ALREADY_EXISTS: u32 = 183;
        let name: Vec<u16> = OsStr::new("Local\\DroneOllamaWorkbench_SingleInstance")
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        unsafe {
            let h = CreateMutexW(std::ptr::null(), 1, name.as_ptr());
            if h.is_null() {
                return false;
            }
            if GetLastError() == ERROR_ALREADY_EXISTS {
                return true;
            }
            // leak handle intentionally for process lifetime
            HELD.store(true, Ordering::SeqCst);
            let _ = HELD;
            return false;
        }
    }
    #[cfg(not(windows))]
    {
        let _ = HELD;
        false
    }
}

fn main() -> eframe::Result<()> {
    std::panic::set_hook(Box::new(|info| {
        write_crash(&format!("PANIC: {info}"));
    }));

    if already_running() {
        write_crash("exit: second instance refused (left existing app running)");
        // show brief message via MessageBox
        #[cfg(windows)]
        {
            use std::ffi::OsStr;
            use std::os::windows::ffi::OsStrExt;
            #[link(name = "user32")]
            extern "system" {
                fn MessageBoxW(
                    hWnd: *mut core::ffi::c_void,
                    lpText: *const u16,
                    lpCaption: *const u16,
                    uType: u32,
                ) -> i32;
            }
            fn wide(s: &str) -> Vec<u16> {
                OsStr::new(s).encode_wide().chain(std::iter::once(0)).collect()
            }
            let t = wide("Drone Ollama is already running.\nThis second launch was cancelled so your session is not killed.");
            let c = wide("Drone Ollama");
            unsafe {
                MessageBoxW(std::ptr::null_mut(), t.as_ptr(), c.as_ptr(), 0x40);
            }
        }
        std::process::exit(0);
    }

    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1280.0, 860.0])
            .with_min_inner_size([960.0, 640.0])
            .with_title("Drone Ollama · Workbench 1.1 (Ollama→drones)"),
        ..Default::default()
    };
    eframe::run_native(
        "Drone Ollama",
        options,
        Box::new(|cc| Ok(Box::new(App::new(cc)))),
    )
}

fn truncate_ui(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        format!(
            "{}\n\n… [truncated for UI stability · full JSON on disk] …\n{}",
            &s[..max / 2],
            &s[s.len() - max / 2..]
        )
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Tab {
    Command,
    Chat,
    Drones,
    Swarm,
    System,
}

#[derive(Debug)]
enum Job {
    EnsureOllama,
    RefreshOllama,
    /// MAIN path: user text → Ollama commander → mount → all 24 drones
    Delegate {
        host: String,
        model: String,
        goal: String,
        lm_on_drones: bool,
        auto_mount: bool,
    },
    Chat {
        host: String,
        model: String,
        history: Vec<(String, String)>,
    },
    Mount,
    MountStatus,
    DroneRun {
        goal: String,
        lm: bool,
    },
    DroneSwarm {
        goals: Vec<String>,
    },
    GoLive,
    Stats,
    Info,
}

#[derive(Debug)]
enum Msg {
    Log(String),
    Ollama {
        ok: bool,
        detail: String,
        models: Vec<String>,
        version: Option<String>,
    },
    ChatReply {
        text: String,
        ms: u128,
        err: Option<String>,
    },
    JsonResult {
        title: String,
        pretty: String,
        ok: bool,
    },
    Busy(bool),
}

struct App {
    tab: Tab,
    host: String,
    model: String,
    models: Vec<String>,
    ollama_ok: bool,
    ollama_detail: String,
    ollama_version: String,
    /// MAIN command input — always wired Ollama → drones
    main_input: String,
    last_commander_brief: String,
    chat_input: String,
    chat_log: Vec<(String, String)>, // role, text
    drone_goal: String,
    swarm_goals: String,
    use_lm: bool,
    auto_mount: bool,
    status_line: String,
    log: Vec<String>,
    last_json: String,
    last_json_title: String,
    drones_mounted: Option<u64>,
    busy: bool,
    auto_ensure_done: bool,
    tx: Sender<Job>,
    rx: Receiver<Msg>,
    last_poll: Instant,
}

impl App {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let mut style = (*cc.egui_ctx.style()).clone();
        style.visuals = egui::Visuals::dark();
        // accent
        style.visuals.selection.bg_fill = egui::Color32::from_rgb(20, 120, 180);
        cc.egui_ctx.set_style(style);

        let (job_tx, job_rx) = mpsc::channel::<Job>();
        let (msg_tx, msg_rx) = mpsc::channel::<Msg>();
        thread::spawn(move || worker(job_rx, msg_tx));

        let app = Self {
            tab: Tab::Command,
            host: ollama_wire::DEFAULT_HOST.into(),
            model: "llama3.1:8b".into(),
            models: vec![],
            ollama_ok: false,
            ollama_detail: "starting…".into(),
            ollama_version: String::new(),
            main_input: String::new(),
            last_commander_brief: String::new(),
            chat_input: String::new(),
            chat_log: vec![(
                "system".into(),
                "MAIN INPUT path: YOU → Ollama commander → task pack → all 24 drones (L||R). Chat tab is chat-only."
                    .into(),
            )],
            drone_goal: "build a worker path package with tools".into(),
            swarm_goals: "swarm A: intake artifact | swarm B: verify seal | swarm C: package evidence"
                .into(),
            use_lm: true,
            auto_mount: true,
            status_line: "Ensuring Ollama…".into(),
            log: vec![
                "app open".into(),
                "main input wire: Ollama commander → drone delegation".into(),
            ],
            last_json: String::new(),
            last_json_title: "Results".into(),
            drones_mounted: None,
            busy: false,
            auto_ensure_done: false,
            tx: job_tx,
            rx: msg_rx,
            last_poll: Instant::now() - Duration::from_secs(60),
        };
        let _ = app.tx.send(Job::EnsureOllama);
        app
    }

    fn push_log(&mut self, s: impl Into<String>) {
        let ts = chrono::Local::now().format("%H:%M:%S");
        self.log.push(format!("[{ts}] {}", s.into()));
        if self.log.len() > 300 {
            let n = self.log.len() - 300;
            self.log.drain(0..n);
        }
    }

    fn pump(&mut self) {
        loop {
            match self.rx.try_recv() {
                Ok(Msg::Log(s)) => {
                    if let Some(rest) = s.strip_prefix("commander brief: ") {
                        self.last_commander_brief = rest.to_string();
                    }
                    self.push_log(s);
                }
                Ok(Msg::Busy(b)) => {
                    self.busy = b;
                    if b {
                        self.status_line = "Working…".into();
                    }
                }
                Ok(Msg::Ollama {
                    ok,
                    detail,
                    models,
                    version,
                }) => {
                    self.ollama_ok = ok;
                    self.ollama_detail = detail;
                    if !models.is_empty() {
                        self.models = models;
                        if !self.models.iter().any(|m| m == &self.model) {
                            self.model = ollama_wire::pick_default_model(&self.models);
                        }
                    }
                    if let Some(v) = version {
                        self.ollama_version = v;
                    }
                    self.status_line = if ok {
                        format!("Ollama LIVE · {}", self.ollama_detail)
                    } else {
                        format!("Ollama DOWN · {}", self.ollama_detail)
                    };
                    self.auto_ensure_done = true;
                }
                Ok(Msg::ChatReply { text, ms, err }) => {
                    self.busy = false;
                    if let Some(e) = err {
                        self.chat_log
                            .push(("system".into(), format!("ERROR ({ms} ms): {e}")));
                    } else {
                        self.chat_log
                            .push(("assistant".into(), format!("{text}\n\n— {ms} ms")));
                    }
                    self.status_line = format!("chat done {ms} ms");
                }
                Ok(Msg::JsonResult { title, pretty, ok }) => {
                    self.busy = false;
                    self.last_json_title = title.clone();
                    // Cap size — huge go-live JSON can freeze/crash egui text widget
                    self.last_json = truncate_ui(&pretty, 48_000);
                    self.status_line = if ok {
                        format!("{title} OK")
                    } else {
                        format!("{title} failed — see result panel")
                    };
                    // try parse mounted from full pretty if small enough, else truncated is fine
                    if let Ok(v) = serde_json::from_str::<serde_json::Value>(&pretty) {
                        if let Some(m) = v.get("mounted").and_then(|x| x.as_u64()) {
                            self.drones_mounted = Some(m);
                        }
                        if let Some(m) = v
                            .pointer("/checks/rust_mount/mounted")
                            .and_then(|x| x.as_u64())
                        {
                            self.drones_mounted = Some(m);
                        }
                    }
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    self.status_line = "worker thread died".into();
                    break;
                }
            }
        }
        // Do not poll while busy — avoids stacking Ensure/Refresh during long drone jobs
        if !self.busy && self.last_poll.elapsed() > Duration::from_secs(20) {
            self.last_poll = Instant::now();
            let _ = self.tx.send(Job::RefreshOllama);
        }
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.pump();
        if self.busy {
            ctx.request_repaint_after(Duration::from_millis(80));
        } else {
            ctx.request_repaint_after(Duration::from_secs(1));
        }

        // Top bar
        egui::TopBottomPanel::top("top").exact_height(52.0).show(ctx, |ui| {
            ui.add_space(6.0);
            ui.horizontal(|ui| {
                ui.heading("Drone Ollama");
                ui.label(egui::RichText::new("Workbench 1.0").weak());
                ui.separator();
                let (label, color) = if self.ollama_ok {
                    (
                        format!("● OLLAMA LIVE  {}", self.ollama_detail),
                        egui::Color32::from_rgb(70, 210, 120),
                    )
                } else {
                    (
                        format!("● OLLAMA DOWN  {}", self.ollama_detail),
                        egui::Color32::from_rgb(230, 80, 80),
                    )
                };
                ui.colored_label(color, label);
                if !self.ollama_version.is_empty() {
                    ui.label(format!("v{}", self.ollama_version));
                }
                ui.separator();
                if let Some(m) = self.drones_mounted {
                    ui.colored_label(
                        egui::Color32::from_rgb(100, 180, 255),
                        format!("drones mounted {m}/24"),
                    );
                } else {
                    ui.label("drones not mounted");
                }
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if self.busy {
                        ui.spinner();
                        ui.label("busy");
                    }
                    if ui.button("Ensure Ollama").clicked() {
                        let _ = self.tx.send(Job::EnsureOllama);
                    }
                    if ui.button("Refresh").clicked() {
                        let _ = self.tx.send(Job::RefreshOllama);
                    }
                });
            });
        });

        // MAIN COMMAND BAR — always visible: Ollama → all drones
        egui::TopBottomPanel::bottom("main_cmd")
            .exact_height(92.0)
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.colored_label(
                        egui::Color32::from_rgb(120, 200, 255),
                        "MAIN → Ollama → 24 drones",
                    );
                    ui.label(&self.status_line);
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        ui.checkbox(&mut self.auto_mount, "auto-mount");
                        ui.checkbox(&mut self.use_lm, "LM on drones");
                    });
                });
                ui.horizontal(|ui| {
                    let resp = ui.add(
                        egui::TextEdit::singleline(&mut self.main_input)
                            .desired_width(ui.available_width() - 200.0)
                            .hint_text("Type task… Enter = Ollama plans, then all drones execute"),
                    );
                    let send = ui
                        .add_enabled(!self.busy, egui::Button::new("▶ DELEGATE"))
                        .clicked()
                        || (resp.lost_focus()
                            && ui.input(|i| i.key_pressed(egui::Key::Enter))
                            && !self.busy);
                    if send {
                        self.submit_main_delegate();
                    }
                });
                if !self.last_commander_brief.is_empty() {
                    ui.small(format!(
                        "last commander: {}",
                        self.last_commander_brief.chars().take(140).collect::<String>()
                    ));
                }
            });

        // Left nav
        egui::SidePanel::left("nav").exact_width(200.0).show(ctx, |ui| {
            ui.heading("Navigate");
            ui.add_space(8.0);
            ui.selectable_value(&mut self.tab, Tab::Command, "⬡  Command");
            ui.selectable_value(&mut self.tab, Tab::Drones, "⬡  Drones");
            ui.selectable_value(&mut self.tab, Tab::Swarm, "⬡  Swarm");
            ui.selectable_value(&mut self.tab, Tab::Chat, "⬡  Chat only");
            ui.selectable_value(&mut self.tab, Tab::System, "⬡  System");
            ui.add_space(16.0);
            ui.separator();
            ui.label("Controller model");
            egui::ComboBox::from_id_salt("model")
                .selected_text(&self.model)
                .width(180.0)
                .show_ui(ui, |ui| {
                    for m in self.models.clone() {
                        ui.selectable_value(&mut self.model, m.clone(), m);
                    }
                });
            ui.add_space(8.0);
            ui.checkbox(&mut self.use_lm, "Use Ollama LM on drones");
            ui.add_space(12.0);
            if ui.button("Mount 24 drones").clicked() && !self.busy {
                let _ = self.tx.send(Job::Mount);
            }
            if ui.button("Mount status").clicked() && !self.busy {
                let _ = self.tx.send(Job::MountStatus);
            }
            if ui.button("GO LIVE").clicked() && !self.busy {
                let _ = self.tx.send(Job::GoLive);
            }
            ui.add_space(12.0);
            ui.separator();
            ui.small("Install root:");
            ui.small(r"G:\AI-Home\apps\DroneOllama");
            ui.small("Drone root:");
            ui.small(drone_bridge::DRONE_ROOT);
        });

        // Right results
        egui::SidePanel::right("results").default_width(360.0).show(ctx, |ui| {
            ui.heading(&self.last_json_title);
            ui.separator();
            egui::ScrollArea::vertical().show(ui, |ui| {
                ui.add(
                    egui::TextEdit::multiline(&mut self.last_json)
                        .desired_width(f32::INFINITY)
                        .font(egui::TextStyle::Monospace)
                        .interactive(false),
                );
            });
            ui.horizontal(|ui| {
                if ui.button("Open drone out").clicked() {
                    drone_bridge::open_path(
                        r"G:\AI-Home\projects\ai-worker-drone-0.5b\out",
                    );
                }
                if ui.button("Open benchmarks").clicked() {
                    drone_bridge::open_path(
                        r"G:\AI-Home\projects\ai-worker-drone-0.5b\out\benchmarks",
                    );
                }
            });
        });

        // Center
        egui::CentralPanel::default().show(ctx, |ui| {
            match self.tab {
                Tab::Command => self.ui_command(ui),
                Tab::Chat => self.ui_chat(ui),
                Tab::Drones => self.ui_drones(ui),
                Tab::Swarm => self.ui_swarm(ui),
                Tab::System => self.ui_system(ui),
            }
        });
    }
}

impl App {
    fn submit_main_delegate(&mut self) {
        let goal = self.main_input.trim().to_string();
        if goal.is_empty() {
            self.status_line = "type a task in MAIN input".into();
            return;
        }
        if !self.ollama_ok {
            self.status_line = "Ollama DOWN — click Ensure Ollama first".into();
            self.push_log("delegate blocked: ollama not live");
            return;
        }
        self.drone_goal = goal.clone();
        self.main_input.clear();
        self.push_log(format!("MAIN DELEGATE: {goal}"));
        let _ = self.tx.send(Job::Delegate {
            host: self.host.clone(),
            model: self.model.clone(),
            goal,
            lm_on_drones: self.use_lm,
            auto_mount: self.auto_mount,
        });
    }

    fn ui_command(&mut self, ui: &mut egui::Ui) {
        ui.heading("Command center");
        ui.label(
            egui::RichText::new("Main input (bottom bar) → Ollama commander → every drone node")
                .strong(),
        );
        ui.separator();
        ui.group(|ui| {
            ui.label("Wire");
            ui.monospace("1. YOU type task in MAIN bar");
            ui.monospace("2. Ollama (selected model) builds commander JSON pack");
            ui.monospace("3. Auto-mount 24 drones (optional)");
            ui.monospace("4. L||R fabric runs ALL roles with commander brief");
            ui.monospace("5. If commander mode=swarm → multi-goal fan-out");
        });
        ui.add_space(8.0);
        ui.label("Pipeline log");
        egui::ScrollArea::vertical().max_height(320.0).show(ui, |ui| {
            for line in self.log.iter().rev().take(50).rev() {
                ui.monospace(line);
            }
        });
        if !self.last_commander_brief.is_empty() {
            ui.separator();
            ui.heading("Last Ollama commander brief");
            ui.label(&self.last_commander_brief);
        }
    }

    fn ui_chat(&mut self, ui: &mut egui::Ui) {
        ui.heading("Ollama Chat (wired)");
        ui.label("Shared local brain — not one model per drone.");
        ui.separator();
        egui::ScrollArea::vertical()
            .stick_to_bottom(true)
            .max_height(ui.available_height() - 80.0)
            .show(ui, |ui| {
                for (role, text) in &self.chat_log {
                    let color = match role.as_str() {
                        "user" => egui::Color32::from_rgb(180, 220, 255),
                        "assistant" => egui::Color32::from_rgb(200, 255, 200),
                        _ => egui::Color32::from_rgb(180, 180, 180),
                    };
                    ui.colored_label(color, format!("[{role}]"));
                    ui.label(text);
                    ui.add_space(8.0);
                }
            });
        ui.horizontal(|ui| {
            let resp = ui.add(
                egui::TextEdit::singleline(&mut self.chat_input)
                    .desired_width(ui.available_width() - 80.0)
                    .hint_text("Message Ollama…"),
            );
            let send = ui.button("Send").clicked()
                || (resp.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)));
            if send && !self.busy && !self.chat_input.trim().is_empty() {
                let prompt = self.chat_input.trim().to_string();
                self.chat_input.clear();
                self.chat_log.push(("user".into(), prompt.clone()));
                let mut history = Vec::new();
                for (r, t) in &self.chat_log {
                    if r == "user" || r == "assistant" {
                        let role = if r == "user" { "user" } else { "assistant" };
                        // strip latency footer for history
                        let clean = t.split("\n\n— ").next().unwrap_or(t).to_string();
                        history.push((role.into(), clean));
                    }
                }
                let _ = self.tx.send(Job::Chat {
                    host: self.host.clone(),
                    model: self.model.clone(),
                    history,
                });
            }
        });
    }

    fn ui_drones(&mut self, ui: &mut egui::Ui) {
        ui.heading("24 Worker Drones");
        ui.label("Controllable dual-hemisphere fabric · tools on · optional shared Ollama LM");
        ui.separator();
        ui.label("Mission goal");
        ui.add(
            egui::TextEdit::multiline(&mut self.drone_goal)
                .desired_width(f32::INFINITY)
                .desired_rows(3),
        );
        ui.horizontal(|ui| {
            if ui
                .add_enabled(!self.busy, egui::Button::new("▶ Run drones"))
                .clicked()
            {
                let _ = self.tx.send(Job::DroneRun {
                    goal: self.drone_goal.clone(),
                    lm: self.use_lm && self.ollama_ok,
                });
            }
            if ui
                .add_enabled(!self.busy, egui::Button::new("Mount then run"))
                .clicked()
            {
                let _ = self.tx.send(Job::Mount);
                let _ = self.tx.send(Job::DroneRun {
                    goal: self.drone_goal.clone(),
                    lm: self.use_lm && self.ollama_ok,
                });
            }
            if ui.button("Open workspace root").clicked() {
                drone_bridge::open_path(
                    r"G:\AI-Home\projects\ai-worker-drone-0.5b\data\workspace",
                );
            }
        });
        ui.add_space(12.0);
        ui.separator();
        ui.heading("Activity log");
        egui::ScrollArea::vertical().max_height(280.0).show(ui, |ui| {
            for line in self.log.iter().rev().take(40).rev() {
                ui.monospace(line);
            }
        });
    }

    fn ui_swarm(&mut self, ui: &mut egui::Ui) {
        ui.heading("Swarm");
        ui.label("Multi-goal fan-out · goals separated by |");
        ui.separator();
        ui.add(
            egui::TextEdit::multiline(&mut self.swarm_goals)
                .desired_width(f32::INFINITY)
                .desired_rows(4),
        );
        if ui
            .add_enabled(!self.busy, egui::Button::new("▶ Launch swarm"))
            .clicked()
        {
            let goals: Vec<String> = self
                .swarm_goals
                .split('|')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect();
            let _ = self.tx.send(Job::DroneSwarm { goals });
        }
        if ui
            .add_enabled(!self.busy, egui::Button::new("🚀 FULL GO-LIVE"))
            .clicked()
        {
            let _ = self.tx.send(Job::GoLive);
        }
        ui.add_space(8.0);
        ui.label("Go-live = Ollama + tools + fabric + swarm + hive + rust mount seal");
    }

    fn ui_system(&mut self, ui: &mut egui::Ui) {
        ui.heading("System");
        ui.separator();
        ui.horizontal(|ui| {
            ui.label("Ollama host");
            ui.text_edit_singleline(&mut self.host);
        });
        ui.label(format!("Models: {}", self.models.join(", ")));
        ui.add_space(8.0);
        if ui.button("Drone info JSON").clicked() && !self.busy {
            let _ = self.tx.send(Job::Info);
        }
        if ui.button("Fabric stats").clicked() && !self.busy {
            let _ = self.tx.send(Job::Stats);
        }
        if ui.button("Open app install folder").clicked() {
            drone_bridge::open_path(r"G:\AI-Home\apps\DroneOllama");
        }
        if ui.button("Open OPERATIONAL_SEAL").clicked() {
            drone_bridge::open_path(
                r"G:\AI-Home\projects\ai-worker-drone-0.5b\out\OPERATIONAL_SEAL.json",
            );
        }
        ui.add_space(12.0);
        ui.group(|ui| {
            ui.label("Honesty");
            ui.label("• Controllable drones — not 24 full LLMs");
            ui.label("• One shared Ollama brain when LM enabled");
            ui.label("• Learn-from-build memory on disk");
            ui.label("• Not a human brain simulation");
        });
    }
}

fn worker(rx: Receiver<Job>, tx: Sender<Msg>) {
    let send_log = |tx: &Sender<Msg>, s: String| {
        let _ = tx.send(Msg::Log(s));
    };
    while let Ok(job) = rx.recv() {
        let _ = tx.send(Msg::Busy(true));
        // Isolate panics so one bad job cannot kill the UI process
        let job_name = format!("{job:?}");
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            run_job(job, &tx, send_log)
        }));
        if let Err(payload) = result {
            let msg = if let Some(s) = payload.downcast_ref::<&str>() {
                (*s).to_string()
            } else if let Some(s) = payload.downcast_ref::<String>() {
                s.clone()
            } else {
                "unknown panic in worker".into()
            };
            write_crash(&format!("worker panic during {job_name}: {msg}"));
            let _ = tx.send(Msg::JsonResult {
                title: "Worker error".into(),
                pretty: format!("Job panicked (app stayed open):\n{msg}\n\nSee logs\\crash.log"),
                ok: false,
            });
            let _ = tx.send(Msg::Log(format!("worker recovered from panic: {msg}")));
        }
        let _ = tx.send(Msg::Busy(false));
    }
}

fn run_job(job: Job, tx: &Sender<Msg>, send_log: impl Fn(&Sender<Msg>, String)) {
        match job {
            Job::EnsureOllama => {
                send_log(&tx, "ensuring Ollama…".into());
                let st = ollama_wire::ensure_ollama();
                let _ = tx.send(Msg::Ollama {
                    ok: st.ok,
                    detail: st.detail,
                    models: st.models,
                    version: st.version,
                });
            }
            Job::RefreshOllama => {
                let st = ollama_wire::status(ollama_wire::DEFAULT_HOST);
                let _ = tx.send(Msg::Ollama {
                    ok: st.ok,
                    detail: st.detail,
                    models: st.models,
                    version: st.version,
                });
            }
            Job::Delegate {
                host,
                model,
                goal,
                lm_on_drones,
                auto_mount,
            } => {
                send_log(
                    &tx,
                    format!("DELEGATE step1 Ollama commander model={model}"),
                );
                // 1) Ollama commander
                let plan = match ollama_wire::commander_plan(&host, &model, &goal) {
                    Ok((raw, plan, ms)) => {
                        send_log(
                            &tx,
                            format!(
                                "DELEGATE step1 OK {ms}ms mission={} mode={}",
                                plan.mission, plan.mode
                            ),
                        );
                        let _ = tx.send(Msg::Log(format!(
                            "commander brief: {}",
                            plan.brief.chars().take(200).collect::<String>()
                        )));
                        // surface plan JSON
                        let _ = tx.send(Msg::JsonResult {
                            title: "Ollama commander pack".into(),
                            pretty: format!(
                                "{{\n  \"raw_preview\": {},\n  \"mission\": {},\n  \"mode\": {},\n  \"brief\": {},\n  \"subtasks\": {},\n  \"composed_goal\": {}\n}}",
                                serde_json::to_string(&raw.chars().take(1200).collect::<String>()).unwrap_or_default(),
                                serde_json::to_string(&plan.mission).unwrap_or_default(),
                                serde_json::to_string(&plan.mode).unwrap_or_default(),
                                serde_json::to_string(&plan.brief).unwrap_or_default(),
                                serde_json::to_string(&plan.subtasks).unwrap_or_default(),
                                serde_json::to_string(&plan.composed_goal).unwrap_or_default(),
                            ),
                            ok: true,
                        });
                        plan
                    }
                    Err(e) => {
                        send_log(&tx, format!("DELEGATE step1 FAIL: {e}"));
                        let _ = tx.send(Msg::JsonResult {
                            title: "Ollama commander".into(),
                            pretty: format!("commander failed: {e}"),
                            ok: false,
                        });
                        return;
                    }
                };

                // 2) mount all drones
                if auto_mount {
                    send_log(&tx, "DELEGATE step2 mount 24 drones".into());
                    match drone_bridge::mount_all() {
                        Ok(v) => {
                            let n = v.get("mounted").and_then(|m| m.as_u64()).unwrap_or(0);
                            send_log(&tx, format!("DELEGATE step2 mounted={n}"));
                        }
                        Err(e) => {
                            send_log(&tx, format!("DELEGATE step2 mount warn: {e} (continuing)"));
                        }
                    }
                }

                // 3) run drones — all nodes get commander brief
                send_log(
                    &tx,
                    format!(
                        "DELEGATE step3 drones mode={} lm_on_drones={lm_on_drones}",
                        plan.mode
                    ),
                );
                let result = if plan.mode == "swarm"
                    && plan.subtasks.len() >= 2
                {
                    let mut goals = plan.subtasks.clone();
                    // prefix each with commander brief for consistency
                    goals = goals
                        .into_iter()
                        .map(|g| format!("{g} | COMMANDER_BRIEF: {}", plan.brief))
                        .collect();
                    drone_bridge::drone_swarm(&goals, 3, "ollama")
                } else {
                    drone_bridge::drone_run_delegated(&plan.composed_goal, lm_on_drones)
                };

                match result {
                    Ok(v) => {
                        let mut out = serde_json::Map::new();
                        out.insert(
                            "pipeline".into(),
                            serde_json::json!({
                                "step1": "ollama_commander",
                                "step2": if auto_mount { "mount_24" } else { "mount_skipped" },
                                "step3": "all_drones_delegated",
                                "model": model,
                                "mission": plan.mission,
                                "mode": plan.mode,
                                "delegate_all_drones": plan.delegate_all_drones,
                            }),
                        );
                        out.insert("commander_brief".into(), serde_json::json!(plan.brief));
                        out.insert("composed_goal".into(), serde_json::json!(plan.composed_goal));
                        out.insert("drone_result".into(), v.clone());
                        let pretty = serde_json::to_string_pretty(&serde_json::Value::Object(out))
                            .unwrap_or_default();
                        let ok = v.get("status").and_then(|s| s.as_str()) == Some("GREEN")
                            || v.get("status").and_then(|s| s.as_str()) == Some("PARTIAL");
                        send_log(
                            &tx,
                            format!(
                                "DELEGATE done status={}",
                                v.get("status").and_then(|s| s.as_str()).unwrap_or("?")
                            ),
                        );
                        let _ = tx.send(Msg::JsonResult {
                            title: "Delegate: Ollama → 24 drones".into(),
                            pretty,
                            ok,
                        });
                    }
                    Err(e) => {
                        send_log(&tx, format!("DELEGATE step3 FAIL: {e}"));
                        let _ = tx.send(Msg::JsonResult {
                            title: "Delegate drones".into(),
                            pretty: e,
                            ok: false,
                        });
                    }
                }
            }
            Job::Chat {
                host,
                model,
                history,
            } => {
                send_log(&tx, format!("chat → {model}"));
                // keep last few turns only
                let hist: Vec<(String, String)> = history.into_iter().rev().take(12).collect();
                let hist: Vec<(String, String)> = hist.into_iter().rev().collect();
                let system = "You are the shared Ollama brain for Drone Ollama Workbench on BOSS. Be concrete.";
                match ollama_wire::chat(&host, &model, &hist, system) {
                    Ok((text, ms)) => {
                        let _ = tx.send(Msg::ChatReply {
                            text,
                            ms,
                            err: None,
                        });
                    }
                    Err(e) => {
                        let _ = tx.send(Msg::ChatReply {
                            text: String::new(),
                            ms: 0,
                            err: Some(e),
                        });
                    }
                }
            }
            Job::Mount => {
                send_log(&tx, "mounting 24 drones…".into());
                match drone_bridge::mount_all() {
                    Ok(v) => {
                        let pretty = serde_json::to_string_pretty(&v).unwrap_or_default();
                        let ok = v.get("mounted").and_then(|m| m.as_u64()) == Some(24);
                        let _ = tx.send(Msg::JsonResult {
                            title: "Mount".into(),
                            pretty,
                            ok,
                        });
                    }
                    Err(e) => {
                        let _ = tx.send(Msg::JsonResult {
                            title: "Mount".into(),
                            pretty: e,
                            ok: false,
                        });
                    }
                }
            }
            Job::MountStatus => match drone_bridge::mount_status() {
                Ok(v) => {
                    let pretty = serde_json::to_string_pretty(&v).unwrap_or_default();
                    let _ = tx.send(Msg::JsonResult {
                        title: "Mount status".into(),
                        pretty,
                        ok: true,
                    });
                }
                Err(e) => {
                    let _ = tx.send(Msg::JsonResult {
                        title: "Mount status".into(),
                        pretty: e,
                        ok: false,
                    });
                }
            },
            Job::DroneRun { goal, lm } => {
                let controller = if lm { "ollama" } else { "local" };
                let lm_assist = if lm { "ollama" } else { "none" };
                send_log(
                    &tx,
                    format!("drone run controller={controller} lm={lm_assist}"),
                );
                match drone_bridge::drone_run(&goal, controller, lm_assist) {
                    Ok(v) => {
                        let pretty = serde_json::to_string_pretty(&v).unwrap_or_default();
                        let ok = v.get("status").and_then(|s| s.as_str()) == Some("GREEN");
                        let _ = tx.send(Msg::JsonResult {
                            title: "Drone run".into(),
                            pretty,
                            ok,
                        });
                    }
                    Err(e) => {
                        let _ = tx.send(Msg::JsonResult {
                            title: "Drone run".into(),
                            pretty: e,
                            ok: false,
                        });
                    }
                }
            }
            Job::DroneSwarm { goals } => {
                send_log(&tx, format!("swarm goals={}", goals.len()));
                match drone_bridge::drone_swarm(&goals, 3, "local") {
                    Ok(v) => {
                        let pretty = serde_json::to_string_pretty(&v).unwrap_or_default();
                        let st = v.get("status").and_then(|s| s.as_str()).unwrap_or("RED");
                        let ok = st == "GREEN" || st == "PARTIAL";
                        let _ = tx.send(Msg::JsonResult {
                            title: "Swarm".into(),
                            pretty,
                            ok,
                        });
                    }
                    Err(e) => {
                        let _ = tx.send(Msg::JsonResult {
                            title: "Swarm".into(),
                            pretty: e,
                            ok: false,
                        });
                    }
                }
            }
            Job::GoLive => {
                send_log(&tx, "GO LIVE full operational stack…".into());
                match drone_bridge::drone_go_live() {
                    Ok(v) => {
                        let pretty = serde_json::to_string_pretty(&v).unwrap_or_default();
                        let st = v.get("status").and_then(|s| s.as_str()).unwrap_or("RED");
                        let ok = st == "GREEN" || st == "PARTIAL";
                        let _ = tx.send(Msg::JsonResult {
                            title: "GO LIVE".into(),
                            pretty,
                            ok,
                        });
                    }
                    Err(e) => {
                        let _ = tx.send(Msg::JsonResult {
                            title: "GO LIVE".into(),
                            pretty: e,
                            ok: false,
                        });
                    }
                }
            }
            Job::Stats => match drone_bridge::drone_stats() {
                Ok(v) => {
                    let pretty = serde_json::to_string_pretty(&v).unwrap_or_default();
                    let _ = tx.send(Msg::JsonResult {
                        title: "Stats".into(),
                        pretty,
                        ok: true,
                    });
                }
                Err(e) => {
                    let _ = tx.send(Msg::JsonResult {
                        title: "Stats".into(),
                        pretty: e,
                        ok: false,
                    });
                }
            },
            Job::Info => match drone_bridge::drone_info() {
                Ok(v) => {
                    let pretty = serde_json::to_string_pretty(&v).unwrap_or_default();
                    let _ = tx.send(Msg::JsonResult {
                        title: "Info".into(),
                        pretty,
                        ok: true,
                    });
                }
                Err(e) => {
                    let _ = tx.send(Msg::JsonResult {
                        title: "Info".into(),
                        pretty: e,
                        ok: false,
                    });
                }
            },
        }
}
