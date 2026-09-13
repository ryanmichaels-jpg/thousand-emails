"""Pins the reference world. If generate.py changes shape on purpose, update these numbers in the same commit."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)

def test_generator_is_reproducible():
    subprocess.run([sys.executable, os.path.join(ROOT, "seed", "generate.py")], check=True, capture_output=True)
    with open(os.path.join(ROOT, "seed", "fixtures", "truth", "summary.json")) as f:
        s = json.load(f)
    assert s["accounts"] == {"Prospect": 4000, "Customer": 500, "Former Customer": 500}
    assert 26000 <= s["contacts"] <= 27000
    assert 240 <= s["nb_opportunities_2026"] <= 290
    assert s["gong_calls"] == 3200
    assert s["planted_tags"] > 3000
    assert s["past_customer_links"] > 500
