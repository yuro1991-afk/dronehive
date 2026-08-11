# DroneHive — Install Guide

## 1. Prerequisites

1. **Windows 10/11** (desktop shortcuts + `.exe` path).
2. **Python 3.10+**  
   Install from [python.org](https://www.python.org/downloads/) and enable **“Add python.exe to PATH”**,  
   or use:  
   `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`
3. Optional: **Ollama** running on `127.0.0.1:11434` for LM assist.

## 2. Install from source

```powershell
cd <clone>\dronehive
.\INSTALL.bat
```

What it does:

- `pip install -e ".[desktop]"`
- Writes `out\INSTALL_SEAL.json` with health evidence
- Reminds you if Scripts is not on PATH

Manual equivalent:

```powershell
python -m pip install -U pip setuptools wheel
python -m pip install -e ".[desktop]"
python -m drone app health
```

## 3. Desktop + Start Menu

```powershell
.\INSTALL_DESKTOP.bat
```

- Builds `dist\DroneHive\DroneHive.exe` when PyInstaller succeeds
- Installs to `%LOCALAPPDATA%\Programs\DroneHive\`
- Shortcuts use a reliable Python GUI launcher; frozen exe is copied when present

Uninstall:

```powershell
powershell -File "%LOCALAPPDATA%\Programs\DroneHive\Uninstall-DroneHive.ps1"
```

## 4. Console commands without PATH

```powershell
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
& $py -m drone app health
& $py -m drone app desktop
& $py -m drone app commission
& "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\dronehive.exe" health
```

## 5. Portable workspace (no git checkout)

If the package is installed via wheel and no project tree is found:

- Workspace auto-seeds to `%LOCALAPPDATA%\DroneHive\workspace`
- Configs copied from package seed (`drone/app/seed/configs`)

Override:

```powershell
$env:DRONE_HIVE_ROOT = "D:\MyDroneHiveData"
python -m drone app health
```

## 6. Release zip (maintainers)

```powershell
powershell -File scripts\build_desktop_exe.ps1
powershell -File scripts\package_release.ps1
# → release\DroneHive-<version>-win64.zip
# → release\dronehive-<version>-py3-none-any.whl (if build succeeds)
```

## 7. Verify (evidence)

| Check | Command | Evidence |
|-------|---------|----------|
| Health | `python -m drone app health` | `"ok": true` |
| Commission | `python -m drone app commission` | `out\COMMISSION_SEAL.json` GREEN |
| Install seal | after `INSTALL.bat` | `out\INSTALL_SEAL.json` |
| Desktop | Start Menu / `START_APP.bat` | native window (not browser) |
| EXE | `dist\DroneHive\DroneHive.exe` | file exists after freeze |

```text
false_green: 0
```
