# Contributing to DroneHive

1. Fork / branch from `main`.
2. `python -m pip install -e ".[dev]"`.
3. Keep **Absolute Truth**: no false greens — seals need real artifacts.
4. Run:
   ```powershell
   python -m drone app health
   python -m drone app task --goal "contrib smoke" --lane fast --lm-assist none
   powershell -File scripts\smoke_install.ps1
   ```
5. Do not commit `data/`, `out/`, or secrets.
6. PRs: small, evidence-backed, update docs when behavior changes.
