#!/usr/bin/env python3
"""投資ルールJSONの検査ツール。

使い方:
  python3 -I tools/validate_rules.py knowledge/onil_rules.json \
      [--hypotheses knowledge/onil_hypotheses.json] [--report work/validation_report.json]

担当する範囲（これで「本に書いてある」ことまでは保証できない）:
  1. 形式検証    : 型・必須項目・列挙値・文字数（JSON Schema）
  2. 整合性検証  : ID・統合・参照・数値の確認状態・数値条件の矛盾
  3. 原著照合    : このツールの範囲外。ページ画像との照合（指示書①の絶対ルール5）が別に必要

終了コード: 0=問題なし / 1=データに問題あり / 2=入力・環境の問題（ファイル無し、JSON構文エラー等）
"""
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
except ImportError:
    sys.exit("jsonschema が見つかりません: pip install jsonschema")

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = {"rules": ROOT / "knowledge/schema/book_rules.schema.json",
           "hypotheses": ROOT / "knowledge/schema/hypotheses.schema.json"}


class InputProblem(Exception):
    """入力ファイルや環境の問題（データ内容の問題ではない）"""


def load_json(path):
    try:
        raw = Path(path).read_bytes()
        return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()
    except FileNotFoundError:
        raise InputProblem(f"ファイルが存在しない: {path}")
    except UnicodeDecodeError as e:
        raise InputProblem(f"UTF-8として読めない: {path} ({e})")
    except json.JSONDecodeError as e:
        raise InputProblem(f"JSONの構文エラー: {path} ({e})")
    except OSError as e:
        raise InputProblem(f"読み込めない: {path} ({e})")


def schema_errors(data, kind):
    schema, digest = load_json(SCHEMAS[kind])
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as e:
        raise InputProblem(f"スキーマ自体が不正: {SCHEMAS[kind].name} ({e.message})")
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in Draft202012Validator(schema).iter_errors(data)]
    return errs, digest


def dupes(ids):
    return sorted({i for i in ids if ids.count(i) > 1})


def check_merges(rules, ids):
    p, target = [], {r["id"]: r["merged_into"] for r in rules}
    for r in rules:
        m = r["merged_into"]
        if m is None:
            continue
        if m == r["id"]:
            p.append(f"{r['id']}: 自分自身に統合されている")
        elif m not in ids:
            p.append(f"{r['id']}: 統合先 {m} が存在しない")
        if r["status"] != "merged":
            p.append(f"{r['id']}: merged_into があるのに status が merged でない")
    for start in target:                       # 循環の検出（鎖をたどる）
        seen, cur = [], start
        while target.get(cur):
            if cur in seen:
                if start == min(seen[seen.index(cur):]):   # 同じ循環を1回だけ報告
                    p.append("統合の循環: " + " → ".join(seen[seen.index(cur):] + [cur]))
                break
            seen.append(cur)
            cur = target[cur]
    return p


def check_numeric_state(r):
    """数値条件の有無と、確認状態(numeric_verified / verification_status)の組み合わせ"""
    p, rid, st, nv = [], r["id"], r["verification_status"], r["numeric_verified"]
    if r["conditions"]:
        if st == "not_applicable":
            p.append(f"{rid}: 数値条件があるのに verification_status が not_applicable")
        if st in ("image_checked", "human_verified") and not nv:
            p.append(f"{rid}: {st} なのに numeric_verified が false")
        if st == "needs_human" and nv:
            p.append(f"{rid}: needs_human なのに numeric_verified が true")
        if nv and st not in ("image_checked", "human_verified"):
            p.append(f"{rid}: numeric_verified が true なのに確認状態が {st}")
    else:
        if nv:
            p.append(f"{rid}: 数値条件が無いのに numeric_verified が true")
        if st not in ("not_applicable", "needs_human"):
            p.append(f"{rid}: 数値条件が無いのに verification_status が {st}")
    return p


def check_conditions(r):
    p, rid = [], r["id"]
    for i, c in enumerate(r["conditions"]):
        op, v, vmax = c["operator"], c["value"], c["value_max"]
        if v is None:
            p.append(f"{rid} conditions[{i}]: value が null（数値の無い条件は conditions に入れない）")
        elif op == "between":
            if vmax is None:
                p.append(f"{rid} conditions[{i}]: between なのに value_max が null")
            elif v > vmax:
                p.append(f"{rid} conditions[{i}]: between の下限 {v} が上限 {vmax} より大きい")
        elif vmax is not None:
            p.append(f"{rid} conditions[{i}]: between 以外なのに value_max がある")
    if r["logic"] == "AND":                    # 同じ指標・期間・基準・単位の単純なAND条件だけ検査
        groups = {}
        for c in r["conditions"]:
            if c["value"] is not None:
                groups.setdefault((c["metric"], c["period"], c["comparison_basis"], c["unit"]), []).append(c)
        for key, cs in groups.items():
            lo = hi = None                     # (値, 厳密か)
            for c in cs:
                op, v, vmax = c["operator"], c["value"], c["value_max"]
                cand_lo = {">=": (v, False), ">": (v, True), "==": (v, False), "between": (v, False)}.get(op)
                cand_hi = {"<=": (v, False), "<": (v, True), "==": (v, False),
                           "between": (vmax, False)}.get(op)
                if cand_lo and (lo is None or cand_lo[0] > lo[0] or (cand_lo[0] == lo[0] and cand_lo[1])):
                    lo = cand_lo
                if cand_hi and cand_hi[0] is not None and (
                        hi is None or cand_hi[0] < hi[0] or (cand_hi[0] == hi[0] and cand_hi[1])):
                    hi = cand_hi
            if lo and hi and (lo[0] > hi[0] or (lo[0] == hi[0] and (lo[1] or hi[1]))):
                p.append(f"{rid}: AND条件が矛盾（{key[0]}: 下限 {lo[0]} と上限 {hi[0]} を同時に満たせない）")
    return p


def run(args):
    problems, report = [], {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "files": {}}
    rules_data, digest = load_json(args.rules)
    errs, sdigest = schema_errors(rules_data, "rules")
    report["files"]["rules"] = {"path": str(args.rules), "sha256": digest,
                                "schema": SCHEMAS["rules"].name, "schema_sha256": sdigest}
    problems += [f"[形式] {e}" for e in errs]
    rules = None if errs else rules_data["rules"]   # 形式が崩れていれば、構造に頼る検査はしない

    hyp_data = None
    if args.hypotheses:
        hyp_data, hdigest = load_json(args.hypotheses)
        herrs, hsdigest = schema_errors(hyp_data, "hypotheses")
        report["files"]["hypotheses"] = {"path": str(args.hypotheses), "sha256": hdigest,
                                         "schema": SCHEMAS["hypotheses"].name, "schema_sha256": hsdigest}
        problems += [f"[形式/仮説] {e}" for e in herrs]
        if herrs:
            hyp_data = None

    if rules is not None:
        ids = [r["id"] for r in rules]
        problems += [f"[整合性] IDが重複: {i}" for i in dupes(ids)]
        problems += [f"[整合性] {m}" for m in check_merges(rules, set(ids))]
        for r in rules:
            problems += [f"[整合性] {m}" for m in check_numeric_state(r)]
            problems += [f"[整合性] {m}" for m in check_conditions(r)]
        if hyp_data is not None:
            active = {r["id"] for r in rules if r["status"] == "active" and r["merged_into"] is None}
            hyps = hyp_data["hypotheses"]
            problems += [f"[整合性] 仮説IDが重複: {i}" for i in dupes([h["id"] for h in hyps])]
            for h in hyps:
                for d in h["derived_from"]:
                    if d not in set(ids):
                        problems.append(f"[整合性] {h['id']}: 参照元 {d} が存在しない")
                    elif d not in active:
                        problems.append(f"[整合性] {h['id']}: 参照元 {d} は統合済み。統合先の有効なルールを参照すること")
        report["counts"] = {"rules": len(rules),
                            "active": sum(r["status"] == "active" for r in rules),
                            "with_numbers": sum(bool(r["conditions"]) for r in rules),
                            "needs_human": sum(r["verification_status"] == "needs_human" for r in rules),
                            "hypotheses": len(hyp_data["hypotheses"]) if hyp_data else None}
    elif args.hypotheses:
        problems.append("[注意] 原著ルールの形式エラーがあるため、参照の検査は実行していない")

    report["problems"] = problems
    report["result"] = "OK" if not problems else "NG"
    return problems, report


def main():
    ap = argparse.ArgumentParser(description="投資ルールJSONの検査", usage=__doc__)
    ap.add_argument("rules", help="原著ルールのJSON")
    ap.add_argument("--hypotheses", help="仮説のJSON")
    ap.add_argument("--report", help="結果をJSONで保存するパス（日時・ファイルのハッシュ付き）")
    args = ap.parse_args()
    try:
        problems, report = run(args)
    except InputProblem as e:
        print(f"入力・環境の問題: {e}", file=sys.stderr)
        return 2
    for p in problems:
        print("NG:", p)
    print("OK" if not problems else f"{len(problems)}件の問題")
    if args.report:
        try:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            print(f"レポートを保存できない: {e}", file=sys.stderr)
            return 2
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
