#!/usr/bin/env python
"""SmallTV-Ultra page composer + memory-budget checker.

The ESP8266 can't hold every page at once, so you pick a subset per build.
This tool composes core.yaml + the selected pages/ into one ESPHome config,
compiles it, and reports RAM/Flash usage so you know if it fits BEFORE upload.

Usage:
  python tools/build.py list
  python tools/build.py compile clock stocks
  python tools/build.py upload  clock stocks --device 192.168.219.112
  python tools/build.py measure stocks          # record this page's cost
  python tools/build.py budget  clock stocks    # fast estimate from cache
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = ROOT / "pages"
GEN = ROOT / "generated.build.yaml"          # git-ignored
COSTS = ROOT / "costs.json"

# Safety thresholds (ESP8266). Flash overflow fails at compile; RAM overflow is
# a runtime OOM, so we warn well before 100%.
RAM_WARN, RAM_RISK = 82.0, 90.0
FLASH_WARN = 92.0

# ---- yaml with block-scalar strings so lambdas stay readable ----
import yaml
def _str_rep(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)
yaml.add_representer(str, _str_rep)

DISPLAY_TMPL = {
    "platform": "st7789v", "id": "my_display", "spi_id": "spihwd",
    "model": "custom", "cs_pin": "GPIO15", "dc_pin": "GPIO00", "reset_pin": "GPIO02",
    "height": 240, "width": 240, "offset_height": 0, "offset_width": 0,
    "fragmentation": 30, "eightbitcolor": False, "spi_mode": "mode3",
    "data_rate": 40000000, "update_interval": "never", "auto_clear_enabled": False,
}

def discover():
    pages = {}
    for p in sorted(PAGES_DIR.glob("*/page.yaml")):
        meta = yaml.safe_load(p.read_text(encoding="utf-8"))
        meta["_dir"] = p.parent.name
        pages[p.parent.name] = meta
    return pages

def deep_merge(a, b):
    for k, v in b.items():
        if k in a and isinstance(a[k], list) and isinstance(v, list):
            a[k] += v
        elif k in a and isinstance(a[k], dict) and isinstance(v, dict):
            deep_merge(a[k], v)
        elif k in a:
            raise ValueError(f"page dependency conflict on '{k}'")
        else:
            a[k] = v
    return a

def resolve(sel, pages):
    out = []
    for s in sel:
        if s not in pages:
            sys.exit(f"unknown page '{s}'. Available: {', '.join(pages)}")
        out.append(pages[s])
    return out

def build_dispatch(selected):
    L = ["it.fill(Color(0x000000));",
         "std::string _m = id(disp_mode).state;",
         'if (_m == "Off") { return; }',
         "it.filled_rectangle(0, 0, 240, 6, Color(0x00E5FF));"]
    for i, pg in enumerate(selected):
        kw = "if" if i == 0 else "else if"
        L.append(f'{kw} (_m == "{pg["name"]}") {{')
        L.append((pg.get("render") or "").rstrip())
        L.append("}")
    if selected:   # only valid when there's a preceding if-branch
        L.append('else { it.printf(120, 120, id(font_small), Color(0xFFA0A0), '
                 'TextAlign::CENTER, "no page: %s", _m.c_str()); }')
    return "\n".join(L)

def generate(selected):
    merged = {}
    for pg in selected:
        req = pg.get("requires")
        if req and req.strip():
            deep_merge(merged, yaml.safe_load(req) or {})
    names = [pg["name"] for pg in selected]
    display = dict(DISPLAY_TMPL)
    display["lambda"] = build_dispatch(selected)
    select = {"platform": "template", "name": "mode", "id": "disp_mode",
              "optimistic": True, "restore_value": True,
              "options": names + ["Off"], "initial_option": names[0] if names else "Off"}
    body = dict(merged)
    body["display"] = [display]
    body["select"] = [select]
    text = ("packages:\n  core: !include core.yaml\n\n"
            + yaml.dump(body, sort_keys=False, allow_unicode=True, width=1000))
    GEN.write_text(text, encoding="utf-8")
    return names

def esphome(*args):
    return subprocess.run([sys.executable, "-m", "esphome", *args],
                          cwd=ROOT, capture_output=True, text=True)

def parse_usage(out):
    def grab(kind):
        m = re.search(kind + r":\s*\[[^\]]*\]\s*([\d.]+)%\s*\(used (\d+) bytes from (\d+) bytes\)", out)
        return (float(m[1]), int(m[2]), int(m[3])) if m else None
    return grab("RAM"), grab("Flash")

def compile_build(names):
    generate_names = generate(resolve(names, discover())) if names else generate([])
    print(f"→ composing: {', '.join(generate_names) or '(none)'}")
    r = esphome("compile", GEN.name)
    ram, flash = parse_usage(r.stdout + r.stderr)
    if "SUCCESS" not in (r.stdout + r.stderr) or not ram or not flash:
        print(r.stdout[-2500:]); print(r.stderr[-1500:])
        sys.exit("✗ compile FAILED (see above) — this page set does not build/fit.")
    return ram, flash

def verdict(ram, flash):
    rp, ru, rt = ram; fp, fu, ft = flash
    print(f"\n  RAM   {ru:>6}/{rt} bytes  {rp:5.1f}%   free {rt-ru} B")
    print(f"  Flash {fu:>6}/{ft} bytes  {fp:5.1f}%   free {ft-fu} B")
    if rp >= RAM_RISK:   print(f"\n  ⚠ RISK: RAM {rp:.1f}% ≥ {RAM_RISK}% — likely OOM at runtime. Drop a page.")
    elif rp >= RAM_WARN: print(f"\n  ⚠ WARN: RAM {rp:.1f}% ≥ {RAM_WARN}% — tight; watch stability.")
    elif fp >= FLASH_WARN: print(f"\n  ⚠ WARN: Flash {fp:.1f}% — getting full.")
    else: print(f"\n  ✓ FITS comfortably.")
    return rp < RAM_RISK

def load_costs():
    return json.loads(COSTS.read_text()) if COSTS.exists() else {"baseline": None, "pages": {}}

def cmd_list(a):
    for name, m in discover().items():
        c = load_costs()["pages"].get(m["name"], {})
        cost = f"~{c['ram']//1024}KB RAM" if c else "unmeasured"
        print(f"  {name:10} {m['name']:12} [{cost}]  {(m.get('description') or '').strip().splitlines()[0][:60]}")

def cmd_compile(a):
    verdict(*compile_build(a.pages))

def cmd_upload(a):
    ram, flash = compile_build(a.pages)
    if not verdict(ram, flash):
        sys.exit("✗ refusing to upload: RAM over the safe limit.")
    print(f"\n→ uploading to {a.device} ...")
    r = esphome("upload", GEN.name, "--device", a.device)
    print("✓ OTA done" if "Successfully uploaded" in (r.stdout+r.stderr) else r.stdout[-1200:]+r.stderr[-800:])

def cmd_measure(a):
    pages = discover()
    costs = load_costs()
    print("→ baseline (no pages) ...")
    b_ram, b_flash = compile_build([])
    costs["baseline"] = {"ram": b_ram[1], "flash": b_flash[1]}
    print(f"→ baseline+{a.page} ...")
    p_ram, p_flash = compile_build([a.page])
    d = {"ram": p_ram[1]-b_ram[1], "flash": p_flash[1]-b_flash[1]}
    costs["pages"][pages[a.page]["name"]] = d
    COSTS.write_text(json.dumps(costs, indent=2))
    print(f"✓ {a.page}: +{d['ram']} B RAM, +{d['flash']} B Flash (saved to costs.json)")

def cmd_budget(a):
    pages = discover(); costs = load_costs()
    base = costs.get("baseline")
    if not base:
        sys.exit("no baseline yet — run: python tools/build.py measure <page>")
    ram, flash, unknown = base["ram"], base["flash"], []
    print(f"  {'page':12} {'RAM Δ':>10} {'Flash Δ':>10}")
    for s in a.pages:
        nm = pages[s]["name"]; c = costs["pages"].get(nm)
        if not c: unknown.append(s); print(f"  {s:12} {'?':>10} {'?':>10}"); continue
        ram += c["ram"]; flash += c["flash"]
        print(f"  {s:12} {c['ram']:>10} {c['flash']:>10}")
    rt, ft = 81920, 1023984
    print(f"\n  predicted RAM ≈ {ram}/{rt} ({100*ram/rt:.1f}%), Flash ≈ {flash}/{ft} ({100*flash/ft:.1f}%)")
    if unknown: print(f"  (unmeasured, not counted: {', '.join(unknown)} — run `measure` on them)")
    print("  → run `compile` for the exact number." )

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # so ✓/⚠ don't crash cp949 consoles
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="SmallTV-Ultra page composer + budget checker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    for name in ("compile", "budget"):
        p = sub.add_parser(name); p.add_argument("pages", nargs="+"); p.set_defaults(fn={"compile":cmd_compile,"budget":cmd_budget}[name])
    up = sub.add_parser("upload"); up.add_argument("pages", nargs="+"); up.add_argument("--device", required=True); up.set_defaults(fn=cmd_upload)
    me = sub.add_parser("measure"); me.add_argument("page"); me.set_defaults(fn=cmd_measure)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
