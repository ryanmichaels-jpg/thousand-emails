"""Play folders are contracts: play.yaml (config), skill.md (drafting instructions), templates/step1-5.md
(the mode-3/4 and holdout arm), eval/ (golden cases + rubric)."""
from __future__ import annotations

import os

import yaml

PLAYS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "plays")


def load_play(name: str) -> dict:
    base = os.path.join(PLAYS_DIR, name)
    with open(os.path.join(base, "play.yaml")) as f:
        config = yaml.safe_load(f)
    with open(os.path.join(base, "skill.md")) as f:
        skill = f.read()
    templates = {}
    for step in range(1, 6):
        path = os.path.join(base, "templates", f"step{step}.md")
        if os.path.exists(path):
            with open(path) as f:
                templates[step] = f.read()
    return {"name": name, "config": config, "skill": skill, "templates": templates,
            "skill_version": f"{name}/v{config['version']}"}


def list_plays() -> list[str]:
    return sorted(d for d in os.listdir(PLAYS_DIR)
                  if os.path.exists(os.path.join(PLAYS_DIR, d, "play.yaml")))
