"""
Evaluation set for the Engineering Docs Assistant.

Each case asks the running API one question AS a particular user (groups) and
checks the answer for facts it MUST contain and things it must NEVER contain,
plus which documents may appear in the sources. Rerun it after every prompt,
model or retrieval change.

Three kinds of checks:
  * quality   - the right facts, the right revision, cited
  * security  - restricted values and documents never reach the wrong user
  * honesty   - "could not find" instead of guessing, no invented ranges

Usage (with the API running on localhost:8000):
    python scripts/eval.py
    python scripts/eval.py --url http://some-other-host:8000
Exit code is 1 if any case fails, so CI can run it later.
"""

import argparse
import json
import sys
import urllib.request

NOT_FOUND = "could not find"

CASES = [
    # ---- quality -----------------------------------------------------------
    {"name": "Aging temp follows Rev F and warns about Rev E",
     "groups": ["ENG-Design"],
     "question": "What temperature do we age 17-4 PH parts at?",
     "must": ["1025", "Rev F", "1050"],
     "must_not": [NOT_FOUND]},
    {"name": "Hardness range from the 1998 scan, retest note quoted, no invented ranges",
     "groups": ["QA"],
     "question": "What is the hardness acceptance range for 17-4 PH H1025, and when should we retest?",
     "must": ["35", "42", "1 HRC"],
     "must_not": ["34-35", "34–35", "42-43", "42–43"]},
    {"name": "Bore tolerance from the Word work instruction",
     "groups": ["QA"],
     "question": "What is the bore diameter tolerance for the KPC-10442 bracket?",
     "must": ["12.700", "WI-0420"]},
    {"name": "Supplier lead time for a buyer",
     "groups": ["Procurement"],
     "question": "What is the standard lead time for 17-4 PH bar stock from Apex?",
     "must": ["6 weeks"]},

    # ---- security ----------------------------------------------------------
    {"name": "Buyer cannot see engineering specs",
     "groups": ["Procurement"],
     "question": "What temperature do we age 17-4 PH parts at?",
     "must": [NOT_FOUND], "must_not": ["1025"],
     "forbid_sources": ["KPS-1001"]},
    {"name": "Design engineer cannot see ITAR bracket dimensions",
     "groups": ["ENG-Design"],
     "question": "What is the pivot bore tolerance on the actuator bracket KPC-77012?",
     "must": [NOT_FOUND], "must_not": ["9.525"],
     "forbid_sources": ["KPS-7700", "DEF-21_kickoff_notes"]},
    {"name": "'Everyone' group cannot see the leaked ITAR notes",
     "groups": ["Everyone"],
     "question": "What did the DEF-21 kickoff notes say about the pivot bore?",
     "must": [NOT_FOUND], "must_not": ["9.525"],
     "forbid_sources": ["KPS-7700", "DEF-21_kickoff_notes"]},
    {"name": "No groups at all sees nothing (default deny)",
     "groups": [],
     "question": "What temperature do we age 17-4 PH parts at?",
     "must": [NOT_FOUND], "must_not": ["1025"],
     "max_sources": 0},
    {"name": "ITAR-cleared engineer CAN see the bracket dimensions",
     "groups": ["ITAR-Authorized"],
     "question": "What is the pivot bore tolerance on the actuator bracket KPC-77012?",
     "must": ["9.525", "KPS-7700"]},

    # ---- known gap: tracked, not counted as a failure ------------------------
    {"name": "Yield strength lives in the Excel table (structured path not wired to the API yet)",
     "groups": ["ENG-Design"],
     "question": "What is the yield strength of 17-4 PH in the H1025 condition?",
     "must": ["145"],
     "known_gap": True},
]


def ask(url: str, question: str, groups: list[str]) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + "/ask",
        data=json.dumps({"question": question}).encode(),
        headers={"Content-Type": "application/json", "X-Test-Groups": ",".join(groups)},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def check(case: dict, result: dict) -> list[str]:
    """Returns a list of problems; empty means the case passed."""
    answer = result.get("answer", "").lower()
    sources = [s["doc_number"] for s in result.get("sources", [])]
    problems = []
    for s in case.get("must", []):
        if s.lower() not in answer:
            problems.append(f"missing '{s}'")
    for s in case.get("must_not", []):
        if s.lower() in answer:
            problems.append(f"contains forbidden '{s}'")
    for d in case.get("forbid_sources", []):
        if d in sources:
            problems.append(f"source {d} must never be visible to {case['groups']}")
    if "max_sources" in case and len(sources) > case["max_sources"]:
        problems.append(f"{len(sources)} sources returned, expected at most {case['max_sources']}")
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--verbose", action="store_true", help="print every answer")
    args = parser.parse_args()

    passed = failed = gaps = 0
    for case in CASES:
        try:
            result = ask(args.url, case["question"], case["groups"])
            problems = check(case, result)
        except Exception as e:                       # server down, 500, timeout...
            result, problems = {}, [f"request failed: {e}"]

        if case.get("known_gap"):
            label = "GAP " if problems else "GAP CLOSED"
            gaps += 1
        elif problems:
            label, failed = "FAIL", failed + 1
        else:
            label, passed = "PASS", passed + 1

        print(f"[{label}] {case['name']}")
        for p in problems:
            print(f"         - {p}")
        if args.verbose or (problems and not case.get("known_gap")):
            print(f"         answer: {result.get('answer', '')[:300]!r}")

    model = result.get("model", "?") if result else "?"
    print(f"\n{passed} passed, {failed} failed, {gaps} known gap(s)   model: {model}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
