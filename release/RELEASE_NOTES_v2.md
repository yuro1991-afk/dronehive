## DroneHive 2.0.0

Pro free-form tool agent + lean Rust TUI. **No admin / no UAC.**

### Assets

- `dronehive-2.0.0-py3-none-any.whl` — pip package
- `dronehive-tui-2.0.0-win64.zip` — Rust console binary

### Run TUI

```powershell
# from full clone
.\START_TUI.bat

# or extract tui zip:
.\dronehive-tui.exe --root <path-to-repo>
```

Desktop shortcut: **DroneHive** (normal user).

### Naming

| Name | Role |
|------|------|
| `dronehive` (pip) | Python CLI |
| `dronehive-tui` | Rust command console |

### Pro agent

```powershell
python -m drone app pro --goal "your task"
```

`false_green: 0` · not 24 full LLMs · sandboxed tools · MIT
