"""Run Calbot's semantic routing cases against the configured OpenAI model."""

from __future__ import annotations

import json
import os
from pathlib import Path

from openai import OpenAI

from calbot.assistant.planner import plan_assistant_turn
from calbot.personality import load_personality


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    cases = json.loads((ROOT / "evals" / "routing_cases.json").read_text())
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=30.0, max_retries=2)
    model = os.environ.get("OPENAI_MODEL", "gpt-5.6-terra")
    outcomes = []
    for case in cases:
        plan = plan_assistant_turn(
            openai_client=client,
            model=model,
            messages=case["messages"],
            actor_name="Ezra",
            personality=load_personality(),
            safety_identifier="calbot-routing-eval",
        )
        passed = plan.mode.value == case["expected_mode"]
        outcomes.append(
            {
                "name": case["name"],
                "expected": case["expected_mode"],
                "actual": plan.mode.value,
                "passed": passed,
            }
        )
    print(json.dumps(outcomes, indent=2))
    failures = [outcome for outcome in outcomes if not outcome["passed"]]
    print(f"\n{len(outcomes) - len(failures)}/{len(outcomes)} routing cases passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
