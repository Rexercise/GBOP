"""Build shared Python/Pine predicates and readable canon from one definition.

Only a small, validated expression grammar is accepted. No runtime eval, network,
credentials, or market data are used. Run with --check in CI to detect drift.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "rules/gtop_rules.json"
BEGIN = "// BEGIN GENERATED GTOP RULES"
END = "// END GENERATED GTOP RULES"
OPS = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!="}


def pine_expression(node, parameters):
    if isinstance(node, ast.Name) and node.id in parameters:
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, (bool, int, float)):
        return str(node.value).lower()
    if isinstance(node, ast.Compare):
        operands = [node.left] + node.comparators
        if not all(type(op) in OPS for op in node.ops):
            raise ValueError("Unsupported comparison")
        return "(" + " and ".join(
            f"({pine_expression(a, parameters)} {OPS[type(op)]} {pine_expression(b, parameters)})"
            for a, op, b in zip(operands, node.ops, operands[1:])) + ")"
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        join = " and " if isinstance(node.op, ast.And) else " or "
        return "(" + join.join(pine_expression(v, parameters) for v in node.values) + ")"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return "(not " + pine_expression(node.operand, parameters) + ")"
    arithmetic = {ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
    if isinstance(node, ast.BinOp) and type(node.op) in arithmetic:
        return f"({pine_expression(node.left, parameters)} {arithmetic[type(node.op)]} {pine_expression(node.right, parameters)})"
    if isinstance(node, ast.IfExp):
        return (f"({pine_expression(node.test, parameters)} ? {pine_expression(node.body, parameters)}"
                f" : {pine_expression(node.orelse, parameters)})")
    raise ValueError(f"Unsupported rule expression: {ast.dump(node)}")


def products(spec):
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    rules = {r["id"]: r["text"] for section in spec["sections"] for r in section["rules"]}
    if len(rules) != sum(len(s["rules"]) for s in spec["sections"]):
        raise ValueError("Duplicate rule ID")
    py = ['"""Generated from rules/gtop_rules.json; edit the rule source, then rebuild."""',
          f'RULESET_VERSION = {spec["version"]!r}', f'RULESET_SHA256 = {digest!r}',
          f'TIMEZONE = {spec["timezone"]!r}', f'ASSIGNED = {spec["assigned_timeframes"]!r}',
          f'VARIANT_NAMES = {spec["variant_names"]!r}',
          'RULESET = {"version": RULESET_VERSION, "sha256": RULESET_SHA256}']
    pine = [BEGIN, '// Generated from rules/gtop_rules.json. Do not edit this block.',
            f'string GTOP_RULESET_VERSION = "{spec["version"]}"',
            f'string GTOP_RULESET_SHA256 = "{digest}"']
    names = set()
    for item in spec["expressions"]:
        name, parameters = item["name"], item["parameters"]
        if name in names or not name.isidentifier() or item["rule"] not in rules:
            raise ValueError("Invalid or duplicate predicate")
        names.add(name)
        if any(not key.isidentifier() or typ not in ("float", "bool", "int") for key, typ in parameters.items()):
            raise ValueError("Invalid predicate parameter")
        expression = ast.parse(item["expression"], mode="eval").body
        translated = pine_expression(expression, parameters)
        py += ["", "", f'def {name}({", ".join(parameters)}):',
               f'    """{item["rule"]}; caller must establish candle/timeframe/coverage eligibility."""',
               f'    return {ast.unparse(expression)}']
        pine += ["", f'// {item["rule"]}; caller establishes confirmed timeframe and coverage.',
                 f'f_gtop_{name}({", ".join(typ + " " + key for key, typ in parameters.items())}) =>',
                 "    " + translated]
    pine += ["", "f_gtop_assigned_tf(string sourceTF) =>", "    switch sourceTF"]
    for source, target in spec["assigned_timeframes"].items():
        pine.append(f'        "{spec["pine_timeframes"][source]}" => "{spec["pine_timeframes"][target]}"')
    pine += ['        => ""', END]
    block = "\n".join(pine) + "\n"
    prompt = f'SHARED GTOP RULEBOOK {spec["version"]}\n' + spec['runtime_guidance'] + '\n'
    doc = [f'# {spec["title"]}', '', '**GBOP knows. The Butterfly Tool shows.**', '',
           f'Edition {spec["version"]} · {spec["date"]}', '', spec["scope"], '', spec["authority"], '']
    for section in spec["sections"]:
        doc += ["## " + section["title"], ""]
        for rule in section["rules"]:
            doc += [f'**{rule["id"]}.** {rule["text"]}', ""]
        if section["title"] == "Model 1 and confirmation":
            doc += ['| Selected range | Assigned Model 1 timeframe |', '| --- | --- |']
            for source, target in spec["assigned_timeframes"].items():
                doc.append(f'| {source} | {target} |')
            doc += ['| CBDR six-hour range | M30 |', '']
    doc += ['## Product agreement', '',
            'Both products use the same generated comparison rules and rulebook version. GBOP can inspect finer retained bars and paired assets; Butterfly may have only the displayed symbol and coarser source candles. Equal inputs require equal answers. Less evidence requires a narrower or unverified answer, never invented precision.', '',
            'Butterfly establishes bounded closed-candle Super Soup occurrences and tracks subsequent cleanliness, parent function and adverse extension. Broader finer-source variants and paired SMT remain GBOP capabilities. Neither occurrence nor objective delivery establishes a member execution or profit. A manually selected Model 1 remains unverified unless its local body and assigned timeframe qualify.', '',
            'See [alignment and verification](GTOP_ALIGNMENT.md) for implemented coverage, reproducible checks, and installation status.', '',
            '## Maintaining the rules', '',
            'Change the authoritative rule source, preserve the rule ID, record the owner decision, add a case that distinguishes the old behavior from the new behavior, and rebuild both products. A meaning change needs a new rulebook version. Run the shared fixtures and product regressions before release. Preserve historical rule versions and evidence cutoffs.', '']
    return {ROOT / 'gbop_voice_web/shared_gtop_rules.py': "\n".join(py) + "\n",
            ROOT / 'gbop_voice_web/gtop_shared_rules.txt': prompt,
            ROOT / 'docs/GTOP_RULEBOOK.md': "\n".join(doc),
            ROOT / 'indicators/shared_gtop_rules.pine': block}, block


def build(check=False):
    spec = json.loads(SPEC.read_text())
    outputs, block = products(spec)
    indicator = ROOT / 'indicators/GTOP_Butterfly_Tool.pine'
    if indicator.exists():
        text = indicator.read_text()
        if text.count(BEGIN) != 1 or text.count(END) != 1:
            raise ValueError('Indicator must contain exactly one generated rules block')
        start, end = text.index(BEGIN), text.index(END) + len(END)
        outputs[indicator] = text[:start] + block.rstrip() + text[end:]
        from build_gtop_qa import qa_source
        outputs[ROOT / 'indicators/GTOP_Shared_Rules_QA.pine'] = qa_source(outputs[indicator], block)
    stale = []
    for path, expected in outputs.items():
        if not path.exists() or path.read_text() != expected:
            stale.append(str(path.relative_to(ROOT)))
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(expected)
    if check and stale:
        raise SystemExit('Shared GTOP rule outputs are stale: ' + ', '.join(stale))
    print('Shared GTOP rules: ' + ('verified' if check else 'generated') + f' {spec["version"]}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    build(parser.parse_args().check)
