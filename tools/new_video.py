"""Scaffold a new video project under videos/<slug>.

Usage:
    python tools/new_video.py <slug> [--portrait]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HF_VERSION = "0.8.143"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--portrait", action="store_true", help="1080x1920 instead of 1920x1080")
    args = ap.parse_args()

    project = ROOT / "videos" / args.slug
    if project.exists():
        sys.exit(f"{project} already exists")

    env = dict(os.environ, HYPERFRAMES_SKIP_SKILLS="1")
    resolution = "portrait" if args.portrait else "landscape"
    subprocess.run(
        f"npx -y hyperframes@{HF_VERSION} init {args.slug} --non-interactive --resolution {resolution}",
        cwd=ROOT / "videos", env=env, shell=True, check=True,
    )

    starter = ROOT / "tools" / "starter"
    (project / "source").mkdir(exist_ok=True)
    (project / "assets").mkdir(exist_ok=True)
    shutil.copy(starter / "template.html", project / "source" / "template.html")
    shutil.copy(starter / "script.json", project / "script.json")

    gsap = next((ROOT / "videos").glob("*/assets/gsap.min.js"), None)
    if gsap and gsap.parent.parent != project:
        shutil.copy(gsap, project / "assets" / "gsap.min.js")
    else:
        subprocess.run(
            ["curl", "-sSL", "-o", str(project / "assets" / "gsap.min.js"),
             "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"],
            check=True,
        )

    pkg_path = project / "package.json"
    if pkg_path.exists():
        pkg = json.loads(pkg_path.read_text("utf-8"))
        scripts = pkg.setdefault("scripts", {})
        gpu_flags = {
            "dev": "--browser-gpu",
            "check": "--browser-gpu",
            "render": "--browser-gpu --gpu --quality delivery",
        }
        for name, flags in gpu_flags.items():
            cmd = scripts.get(name)
            if isinstance(cmd, str) and "hyperframes" in cmd and flags not in cmd:
                scripts[name] = cmd.rstrip() + " " + flags
        pkg_path.write_text(json.dumps(pkg, indent=2) + "\n", "utf-8")

    if args.portrait:
        tpl = project / "source" / "template.html"
        text = tpl.read_text("utf-8")
        text = (text.replace('data-resolution="landscape"', 'data-resolution="portrait"')
                    .replace("width=1920, height=1080", "width=1080, height=1920")
                    .replace("width: 1920px; height: 1080px", "width: 1080px; height: 1920px")
                    .replace('data-width="1920" data-height="1080"', 'data-width="1080" data-height="1920"'))
        tpl.write_text(text, "utf-8")

    print(f"created {project.relative_to(ROOT)}")
    print("next: edit script.json + source/template.html, then")
    print(f"  python tools/make_video.py videos/{args.slug}")


if __name__ == "__main__":
    main()
