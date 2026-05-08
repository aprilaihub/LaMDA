#!/usr/bin/env python3
import json
from pathlib import Path

def latest_run():
    xs = sorted(Path("gen_runs").glob("*"), key=lambda p:p.stat().st_mtime)
    if not xs: raise SystemExit("no gen_runs/* found")
    return xs[-1]

def main():
    run = latest_run()
    chat = run / "chat.jsonl"
    if not chat.exists():
        raise SystemExit(f"no {chat}")
    md = run / "chat.md"
    lines = []
    for line in chat.read_text().splitlines():
        try:
            obj = json.loads(line)
            role = obj.get("role","?")
            content = obj.get("content","").strip()
            lines.append(f"### {role}\n\n{content}\n")
        except Exception:
            continue
    md.write_text("\n---\n".join(lines))
    print(f"[ok] wrote {md}")

if __name__ == "__main__":
    main()

