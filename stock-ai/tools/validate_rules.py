#!/usr/bin/env python3
"""使い方: python3 -I tools/validate_rules.py knowledge/onil_rules.json [knowledge/onil_hypotheses.json]
型・必須項目・列挙値・文字数に加え、IDの重複、統合先の存在、仮説の参照先を検査する。"""
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = {"rules": ROOT / "knowledge/schema/book_rules.schema.json",
           "hypotheses": ROOT / "knowledge/schema/hypotheses.schema.json"}

def check(path, kind):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    schema = json.loads(SCHEMAS[kind].read_text(encoding="utf-8"))
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in Draft202012Validator(schema).iter_errors(data)]
    return data, errs

def main(argv):
    if not argv:
        print(__doc__); return 2
    problems = []
    rules, errs = check(argv[0], "rules"); problems += errs
    ids = [r.get("id") for r in rules.get("rules", [])]
    problems += [f"IDが重複: {i}" for i in sorted({x for x in ids if ids.count(x) > 1})]
    for r in rules.get("rules", []):
        if r.get("merged_into") and r["merged_into"] not in ids:
            problems.append(f"{r['id']}: 統合先 {r['merged_into']} が存在しない")
        if r.get("conditions") and r.get("numeric_verified") is False and r.get("verification_status") == "image_checked":
            problems.append(f"{r['id']}: image_checkedなのにnumeric_verifiedがfalse")
    if len(argv) > 1:
        hyp, errs = check(argv[1], "hypotheses"); problems += errs
        for h in hyp.get("hypotheses", []):
            for d in h.get("derived_from", []):
                if d not in ids:
                    problems.append(f"{h['id']}: 参照元 {d} が存在しない")
    for p in problems: print("NG:", p)
    print("OK" if not problems else f"{len(problems)}件の問題")
    return 1 if problems else 0

sys.exit(main(sys.argv[1:]))
