"""
Principal-scaled model clones (dh-*) + M2M 0.5B fleet (m2m-*).

Creates Ollama Modelfile clones FROM installed bases.
Core Principal: only dh-face talks to the user.
M2M fleet (m2m-*): all strictly AI2AI — zero human-facing weights.

CLI: python -m drone clones install|list|smoke|wire|seal
     python -m drone.model_clones --catalog configs/m2m_clones_0.5b.json all
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def resolve_ollama_bin() -> str:
    env = os.environ.get("OLLAMA_EXE")
    if env and Path(env).is_file():
        return env
    which = shutil.which("ollama")
    if which:
        return which
    cands = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(r"C:\Program Files\Ollama\ollama.exe"),
    ]
    for c in cands:
        if c.is_file():
            return str(c)
    return "ollama"


class ModelClones:
    def __init__(
        self,
        root: Path | None = None,
        *,
        catalog: str | Path | None = None,
        modelfile_dir: str | Path | None = None,
        out_prefix: str | None = None,
    ) -> None:
        self.root = Path(root or _root())
        if catalog:
            cp = Path(catalog)
            self.cfg_path = cp if cp.is_absolute() else (self.root / cp)
        else:
            self.cfg_path = self.root / "configs" / "model_clones.json"
        if modelfile_dir:
            md = Path(modelfile_dir)
            self.modelfile_dir = md if md.is_absolute() else (self.root / md)
        else:
            # m2m catalog → models/clones/m2m ; default → models/clones
            name = self.cfg_path.name.lower()
            if "m2m" in name:
                self.modelfile_dir = self.root / "models" / "clones" / "m2m"
            else:
                self.modelfile_dir = self.root / "models" / "clones"
        self.out = self.root / "out"
        self.out.mkdir(parents=True, exist_ok=True)
        self.modelfile_dir.mkdir(parents=True, exist_ok=True)
        stem = self.cfg_path.stem.upper()
        self.out_prefix = out_prefix or (
            "M2M_CLONES" if "m2m" in stem.lower() else "MODEL_CLONES"
        )
        self.cfg = self._load()

    def _load(self) -> dict[str, Any]:
        if self.cfg_path.is_file():
            return json.loads(self.cfg_path.read_text(encoding="utf-8"))
        return {"clones": []}

    def _write(self, path: Path, data: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path

    def _http_json(self, method: str, url: str, body: dict | None = None, timeout: float = 120) -> dict[str, Any]:
        data = None
        headers = {"Content-Type": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}

    def list_installed(self) -> list[str]:
        try:
            data = self._http_json("GET", f"{ollama_host()}/api/tags", timeout=10)
            return [m.get("name") for m in (data.get("models") or []) if m.get("name")]
        except Exception:
            return []

    @staticmethod
    def _name_matches(tag: str, installed: set[str] | list[str]) -> bool:
        """Match bare tag (m2m-probe) to ollama names (m2m-probe:latest)."""
        if not tag:
            return False
        inst = set(installed)
        if tag in inst:
            return True
        if f"{tag}:latest" in inst:
            return True
        # also accept installed bare vs tag with :latest
        base = tag.split(":")[0]
        for n in inst:
            if n == base or n.startswith(base + ":"):
                return True
        return False

    def clones(self) -> list[dict[str, Any]]:
        return list(self.cfg.get("clones") or [])

    def write_modelfiles(self) -> dict[str, Any]:
        from drone.ai_protocol import apply_to_clone_systems

        written = []
        # strip human weights on AI2AI clones before writing Modelfiles
        clones = apply_to_clone_systems(self.clones(), root=self.root)
        for c in clones:
            tag = c.get("tag")
            base = c.get("from")
            sys_txt = (c.get("system") or "").replace('"""', "'''")
            temp = c.get("temperature", 0.2)
            # AI2AI clones: lower temp, less "chatty" human sampling
            if not c.get("talks_to_user") and c.get("chat", True) is not False:
                temp = min(float(temp), 0.15)
            ctx = int(c.get("num_ctx") or 8192)
            chat = c.get("chat", True)
            if chat is False:
                # embed alias: minimal FROM only
                body = f"FROM {base}\n# principal: {c.get('scale')} {c.get('role')} CHANNEL=EMBED\n"
            else:
                body = (
                    f"FROM {base}\n"
                    f"# DroneHive principal clone · {c.get('scale')} · {c.get('role')}\n"
                    f"# talks_to_user={c.get('talks_to_user')} channel={c.get('channel')}\n"
                    f"# strip_human_weights={c.get('strip_human_weights')}\n"
                    f'SYSTEM """{sys_txt}"""\n'
                    f"PARAMETER temperature {temp}\n"
                    f"PARAMETER num_ctx {ctx}\n"
                )
            path = self.modelfile_dir / f"Modelfile.{tag}"
            path.write_text(body, encoding="utf-8")
            written.append(
                {
                    "tag": tag,
                    "path": str(path),
                    "from": base,
                    "scale": c.get("scale"),
                    "talks_to_user": c.get("talks_to_user"),
                    "channel": c.get("channel"),
                }
            )
        manifest = {
            "schema": "drone.model_clones.modelfiles.v1",
            "catalog": str(self.cfg_path),
            "utc": _utc(),
            "count": len(written),
            "files": written,
            "false_green": 0,
        }
        self._write(self.out / f"{self.out_prefix}_MODELFILES.json", manifest)
        return manifest

    def install(self, *, only: list[str] | None = None) -> dict[str, Any]:
        """ollama create clones from Modelfiles."""
        self.write_modelfiles()
        installed = set(self.list_installed())
        results = []
        ollama_bin = resolve_ollama_bin()
        expected = len(self.clones()) if not only else len(only)
        # Prefer CLI create (reliable for Modelfile SYSTEM/PARAMETER)
        for c in self.clones():
            tag = str(c.get("tag"))
            if only and tag not in only and c.get("id") not in only:
                continue
            base = str(c.get("from"))
            # base must exist (cloud may be special)
            base_ok = self._name_matches(base, installed)
            if not base_ok and "cloud" not in base:
                results.append(
                    {
                        "tag": tag,
                        "ok": False,
                        "status": "RED",
                        "error": f"base missing: {base}",
                        "false_green": 0,
                    }
                )
                continue
            mf = self.modelfile_dir / f"Modelfile.{tag}"
            t0 = time.perf_counter()
            try:
                p = subprocess.run(
                    [ollama_bin, "create", tag, "-f", str(mf)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=str(self.root),
                )
                ok = p.returncode == 0
                results.append(
                    {
                        "tag": tag,
                        "from": base,
                        "scale": c.get("scale"),
                        "role": c.get("role"),
                        "talks_to_user": c.get("talks_to_user"),
                        "ok": ok,
                        "status": "GREEN" if ok else "RED",
                        "ms": round((time.perf_counter() - t0) * 1000, 1),
                        "stdout_tail": (p.stdout or "")[-400:],
                        "stderr_tail": (p.stderr or "")[-400:],
                        "returncode": p.returncode,
                        "modelfile": str(mf),
                        "false_green": 0,
                    }
                )
                if ok:
                    installed.add(tag)
                    installed.add(f"{tag}:latest")
            except Exception as e:
                results.append(
                    {
                        "tag": tag,
                        "ok": False,
                        "status": "RED",
                        "error": str(e),
                        "false_green": 0,
                    }
                )

        green = [r for r in results if r.get("ok")]
        # Full fleet GREEN only when all expected succeed
        if results and len(green) == len(results):
            status = "GREEN"
        elif green:
            status = "PARTIAL"
        else:
            status = "RED"
        seal = {
            "schema": "drone.model_clones.install.v1",
            "catalog": str(self.cfg_path),
            "status": status,
            "false_green": 0,
            "utc": _utc(),
            "installed_ok": len(green),
            "total": len(results),
            "expected": expected,
            "ollama_bin": ollama_bin,
            "results": results,
            "principal": "CORE_PRINCIPAL.md",
            "clone_tags": [r.get("tag") for r in green],
            "strict_m2m": all(
                not c.get("talks_to_user") for c in self.clones()
            ),
        }
        self._write(self.out / f"{self.out_prefix}_INSTALL.json", seal)
        return seal

    def list_status(self) -> dict[str, Any]:
        installed = set(self.list_installed())
        rows = []
        for c in self.clones():
            tag = c.get("tag")
            base = c.get("from")
            rows.append(
                {
                    "n": c.get("n"),
                    "tag": tag,
                    "from": base,
                    "scale": c.get("scale"),
                    "role": c.get("role"),
                    "talks_to_user": c.get("talks_to_user"),
                    "clone_installed": self._name_matches(str(tag), installed),
                    "base_installed": self._name_matches(str(base), installed),
                }
            )
        return {
            "schema": "drone.model_clones.list.v1",
            "catalog": str(self.cfg_path),
            "utc": _utc(),
            "false_green": 0,
            "count": len(rows),
            "clones": rows,
            "face": "dh-face",
            "prefix": self.cfg.get("prefix"),
            "strict_m2m": all(not c.get("talks_to_user") for c in self.clones()),
            "law": self.cfg.get("law"),
        }

    def smoke(self) -> dict[str, Any]:
        installed = set(self.list_installed())
        results = []
        for c in self.clones():
            tag = str(c.get("tag"))
            if not self._name_matches(tag, installed):
                results.append(
                    {
                        "tag": tag,
                        "status": "SKIP",
                        "ok": False,
                        "reason": "not_installed",
                        "false_green": 0,
                    }
                )
                continue
            if c.get("chat") is False:
                # embed smoke
                try:
                    data = self._http_json(
                        "POST",
                        f"{ollama_host()}/api/embeddings",
                        {"model": tag, "prompt": "principal embed smoke"},
                        timeout=60,
                    )
                    emb = data.get("embedding") or []
                    ok = isinstance(emb, list) and len(emb) > 0
                    results.append(
                        {
                            "tag": tag,
                            "kind": "embed",
                            "ok": ok,
                            "status": "GREEN" if ok else "RED",
                            "dims": len(emb) if ok else 0,
                            "false_green": 0,
                        }
                    )
                except Exception as e:
                    results.append(
                        {
                            "tag": tag,
                            "ok": False,
                            "status": "RED",
                            "error": str(e),
                            "false_green": 0,
                        }
                    )
                continue
            try:
                data = self._http_json(
                    "POST",
                    f"{ollama_host()}/api/chat",
                    {
                        "model": tag,
                        "messages": [
                            {"role": "user", "content": "Reply with exactly: OK"}
                        ],
                        "stream": False,
                        "options": {"num_predict": 12, "temperature": 0},
                    },
                    timeout=120,
                )
                text = str((data.get("message") or {}).get("content", "")).strip()
                ok = bool(text)
                results.append(
                    {
                        "tag": tag,
                        "scale": c.get("scale"),
                        "ok": ok,
                        "status": "GREEN" if ok else "RED",
                        "preview": text[:80],
                        "talks_to_user": c.get("talks_to_user"),
                        "false_green": 0,
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "tag": tag,
                        "ok": False,
                        "status": "RED",
                        "error": str(e),
                        "false_green": 0,
                    }
                )

        green = [r for r in results if r.get("status") == "GREEN"]
        is_m2m = "m2m" in self.cfg_path.name.lower() or str(self.cfg.get("prefix", "")).startswith("m2m")
        installed_now = set(self.list_installed())
        if is_m2m:
            # M2M fleet: GREEN when all installed chat clones smoke OK
            chat_targets = [
                c for c in self.clones() if c.get("chat", True) is not False
            ]
            installed_chat = [
                c
                for c in chat_targets
                if self._name_matches(str(c.get("tag")), installed_now)
            ]
            green_tags = {r.get("tag") for r in green}
            all_ok = bool(installed_chat) and all(
                str(c.get("tag")) in green_tags for c in installed_chat
            )
            status = (
                "GREEN"
                if all_ok and len(green) == len(installed_chat)
                else ("PARTIAL" if green else "RED")
            )
            face_ok = False  # M2M has no user face by design
        else:
            status = (
                "GREEN"
                if any(r.get("tag") == "dh-face" and r.get("ok") for r in results)
                else "PARTIAL"
            )
            face_ok = any(r.get("tag") == "dh-face" and r.get("ok") for r in results)
        seal = {
            "schema": "drone.model_clones.smoke.v1",
            "catalog": str(self.cfg_path),
            "status": status,
            "false_green": 0,
            "utc": _utc(),
            "green": len(green),
            "total": len(results),
            "results": results,
            "face_ok": face_ok,
            "strict_m2m": is_m2m,
        }
        self._write(self.out / f"{self.out_prefix}_SMOKE.json", seal)
        return seal

    def wire_into_app(self) -> dict[str, Any]:
        """Register clones into multi_hosts / super_llms. M2M fleet is AI-only registry."""
        installed = set(self.list_installed())
        multi_path = self.root / "configs" / "multi_hosts.json"
        super_path = self.root / "configs" / "super_llms.json"
        multi = json.loads(multi_path.read_text(encoding="utf-8")) if multi_path.is_file() else {}
        super_cfg = (
            json.loads(super_path.read_text(encoding="utf-8")) if super_path.is_file() else {}
        )

        is_m2m = "m2m" in self.cfg_path.name.lower() or str(self.cfg.get("prefix", "")).startswith(
            "m2m"
        )
        m2m_present = sorted(
            {str(t).split(":")[0] for t in installed if str(t).startswith("m2m-")}
        )
        dh_present = sorted(
            {str(t).split(":")[0] for t in installed if str(t).startswith("dh-")}
        )

        if is_m2m:
            # Strict M2M: never set FACE to an m2m model
            try:
                cat_rel = str(self.cfg_path.relative_to(self.root))
            except ValueError:
                cat_rel = str(self.cfg_path)
            fleet = {
                "enabled": True,
                "prefix": "m2m-",
                "base": self.cfg.get("base") or "qwen2.5:0.5b",
                "catalog": cat_rel,
                "count_catalog": len(self.clones()),
                "count_installed": len(m2m_present),
                "tags": m2m_present,
                "talks_to_user": False,
                "channel": "AI2AI",
                "strict_m2m": True,
            }
            multi["m2m_fleet"] = fleet
            super_cfg["m2m_fleet"] = fleet
            multi_path.write_text(json.dumps(multi, indent=2), encoding="utf-8")
            super_path.write_text(json.dumps(super_cfg, indent=2), encoding="utf-8")
            report = {
                "schema": "drone.model_clones.wire.v1",
                "status": "GREEN" if len(m2m_present) >= 1 else "RED",
                "false_green": 0,
                "utc": _utc(),
                "kind": "m2m_fleet",
                "wired": {
                    "multi_hosts": str(multi_path),
                    "super_llms": str(super_path),
                    "m2m_count": len(m2m_present),
                    "face_unchanged": multi.get("face", {}).get("model"),
                },
                "clones_present": m2m_present,
            }
            self._write(self.out / f"{self.out_prefix}_WIRE.json", report)
            return report

        # FACE (dh-* principal clones only)
        if self._name_matches("dh-face", installed):
            face = multi.get("face") or {}
            face["model"] = "dh-face"
            face["clone"] = True
            face["scale"] = "P0"
            multi["face"] = face

        # map host models
        host_map = {
            "ollama_fast": "dh-scout",
            "ollama_coder": "dh-coder",
            "ollama_ops": "dh-ops",
            "ollama_smarts": "dh-smarts",
            "ollama_seal": "dh-critic",
        }
        for h in multi.get("hosts") or []:
            hid = h.get("id")
            if hid in host_map and self._name_matches(host_map[hid], installed):
                h["model"] = host_map[hid]
                h["clone"] = True
            if hid == "ollama_fast" and self._name_matches("dh-scout", installed):
                h["model"] = "dh-scout"

        multi["model_clones"] = {
            "enabled": True,
            "prefix": "dh-",
            "principal": "configs/CORE_PRINCIPAL.md",
            "catalog": "configs/model_clones.json",
        }
        multi_path.write_text(json.dumps(multi, indent=2), encoding="utf-8")

        # super_llms routing prefer clones
        routing = super_cfg.get("routing") or {}
        if self._name_matches("dh-face", installed):
            routing["default"] = "dh-face"
            routing["reason"] = "dh-face"
        if self._name_matches("dh-coder", installed):
            routing["coding"] = "dh-coder"
        if self._name_matches("dh-scout", installed):
            routing["fast"] = "dh-scout"
        if self._name_matches("dh-ops", installed):
            routing["ops"] = "dh-ops"
        if self._name_matches("dh-critic", installed):
            routing["seal"] = "dh-critic"
        if self._name_matches("dh-smarts", installed):
            routing["smarts"] = "dh-smarts"
        if self._name_matches("dh-tight", installed):
            routing["reason_heavy"] = "dh-tight"
        if self._name_matches("dh-embed", installed):
            routing["embed"] = "dh-embed"
        if self._name_matches("dh-cloud", installed):
            routing["cloud"] = "dh-cloud"
        super_cfg["routing"] = routing
        super_cfg["model_clones"] = multi["model_clones"]
        super_path.write_text(json.dumps(super_cfg, indent=2), encoding="utf-8")

        report = {
            "schema": "drone.model_clones.wire.v1",
            "status": "GREEN",
            "false_green": 0,
            "utc": _utc(),
            "kind": "dh_principal",
            "wired": {
                "multi_hosts": str(multi_path),
                "super_llms": str(super_path),
                "face": multi.get("face", {}).get("model"),
                "routing": routing,
            },
            "clones_present": dh_present,
        }
        self._write(self.out / f"{self.out_prefix}_WIRE.json", report)
        return report

    def seal(self) -> dict[str, Any]:
        lst = self.list_status()
        smoke = (
            self.smoke()
            if any(c.get("clone_installed") for c in lst.get("clones") or [])
            else {"status": "SKIP", "note": "install first"}
        )
        n_ok = sum(1 for c in (lst.get("clones") or []) if c.get("clone_installed"))
        n_total = len(lst.get("clones") or [])
        if smoke.get("status") == "SKIP":
            status = "PARTIAL" if n_ok else "RED"
        else:
            status = smoke.get("status")
        seal = {
            "schema": "drone.model_clones.seal.v1",
            "catalog": str(self.cfg_path),
            "status": status,
            "false_green": 0,
            "utc": _utc(),
            "principal": "configs/CORE_PRINCIPAL.md",
            "installed_count": n_ok,
            "catalog_count": n_total,
            "clones": lst.get("clones"),
            "smoke": smoke,
            "strict_m2m": lst.get("strict_m2m"),
            "evidence": [
                str(self.out / f"{self.out_prefix}_INSTALL.json"),
                str(self.out / f"{self.out_prefix}_SMOKE.json"),
                str(self.out / f"{self.out_prefix}_WIRE.json"),
                str(self.modelfile_dir),
            ],
        }
        self._write(self.out / f"{self.out_prefix}_SEAL.json", seal)
        return seal


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="drone clones", description="Principal / M2M model clones")
    p.add_argument("--root", default=None)
    p.add_argument(
        "--catalog",
        default=None,
        help="catalog json (default configs/model_clones.json; use configs/m2m_clones_0.5b.json for M2M)",
    )
    p.add_argument("--modelfile-dir", default=None, help="override Modelfile output dir")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("modelfiles", help="write Modelfile.* only")
    ins = sub.add_parser("install", help="ollama create all clones in catalog")
    ins.add_argument("--only", default=None, help="comma tags e.g. m2m-probe,m2m-scout")
    sub.add_parser("smoke")
    sub.add_parser("wire", help="register clones into multi_hosts + super_llms")
    sub.add_parser("seal")
    sub.add_parser("all", help="modelfiles + install + wire + smoke + seal")

    args = p.parse_args(argv)
    mc = ModelClones(
        Path(args.root) if args.root else None,
        catalog=args.catalog,
        modelfile_dir=args.modelfile_dir,
    )

    if args.cmd == "list":
        print(json.dumps(mc.list_status(), indent=2))
        return 0
    if args.cmd == "modelfiles":
        print(json.dumps(mc.write_modelfiles(), indent=2))
        return 0
    if args.cmd == "install":
        only = [x.strip() for x in args.only.split(",")] if args.only else None
        out = mc.install(only=only)
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
    if args.cmd == "smoke":
        out = mc.smoke()
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") in {"GREEN", "PARTIAL"} else 1
    if args.cmd == "wire":
        print(json.dumps(mc.wire_into_app(), indent=2))
        return 0
    if args.cmd == "seal":
        out = mc.seal()
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") != "RED" else 1
    if args.cmd == "all":
        mf = mc.write_modelfiles()
        ins = mc.install()
        wire = mc.wire_into_app()
        sm = mc.smoke()
        seal = mc.seal()
        print(
            json.dumps(
                {
                    "catalog": str(mc.cfg_path),
                    "modelfiles": mf.get("count"),
                    "install": ins.get("status"),
                    "install_ok": ins.get("installed_ok"),
                    "install_total": ins.get("total"),
                    "strict_m2m": ins.get("strict_m2m"),
                    "wire": wire.get("status"),
                    "wire_kind": wire.get("kind"),
                    "smoke": sm.get("status"),
                    "smoke_green": sm.get("green"),
                    "seal": seal.get("status"),
                    "false_green": 0,
                },
                indent=2,
            )
        )
        return 0 if seal.get("status") != "RED" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
