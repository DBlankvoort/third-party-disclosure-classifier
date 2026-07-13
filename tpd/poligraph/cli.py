"""PoliGraph CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .graph import PoliGraph
from .poligrapher import PoliGrapher


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def _load_graph(path: str) -> PoliGraph:
    return PoliGraph.from_dict(json.loads(_read(path)))


def cmd_build(args) -> int:
    text = _read(args.input)
    pid = args.policy_id or Path(args.input).stem
    grapher = PoliGrapher()
    g = grapher.from_text(text, pid) if args.text else grapher.from_html(text, pid)
    out = g.to_json()
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"{g}\nwritten to {args.output}", file=sys.stderr)
    else:
        print(out)
    return 0


def cmd_contradictions(args) -> int:
    from .applications import find_contradictions, format_contradictions
    from .ontology import global_data_ontology, global_entity_ontology
    g = _load_graph(args.graph)
    cons = find_contradictions(g, global_data_ontology(), global_entity_ontology())
    print(format_contradictions(cons))
    return 0


def cmd_terms(args) -> int:
    from .applications import check_terms, format_report
    g = _load_graph(args.graph)
    print(format_report(g, check_terms(g)))
    return 0


def cmd_flows(args) -> int:
    from .applications import analyze_flows
    g = _load_graph(args.graph)
    flows = []
    for spec in args.flow:
        entity, _, data = spec.partition(":")
        flows.append((entity.replace("_", " "), data.replace("_", " ")))
    for r in analyze_flows(g, flows):
        print(f"({r.entity}, {r.data_type}) -> {r.disclosure.value}  "
              f"purpose_class={r.purpose_class}")
    return 0


def cmd_ontology(args) -> int:
    from .ontology import global_data_ontology, global_entity_ontology
    ont = global_data_ontology() if args.kind == "data" else global_entity_ontology()
    for hyper, hypo in sorted(ont.g.edges()):
        print(f"{hyper}  ->  {hypo}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="poligraph", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="build a PoliGraph from a policy")
    b.add_argument("input")
    b.add_argument("--text", action="store_true", help="treat input as plain text, not HTML")
    b.add_argument("--policy-id")
    b.add_argument("-o", "--output")
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("contradictions", help="find contradictions in a PoliGraph")
    c.add_argument("graph")
    c.set_defaults(func=cmd_contradictions)

    t = sub.add_parser("terms", help="check term-definition correctness")
    t.add_argument("graph")
    t.set_defaults(func=cmd_terms)

    f = sub.add_parser("flows", help="flow-to-policy consistency check")
    f.add_argument("graph")
    f.add_argument("--flow", action="append", default=[],
                   metavar="ENTITY:DATA", help="e.g. advertiser:ip_address")
    f.set_defaults(func=cmd_flows)

    o = sub.add_parser("ontology", help="print a global ontology")
    o.add_argument("kind", choices=["data", "entity"])
    o.set_defaults(func=cmd_ontology)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
