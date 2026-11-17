#!/usr/bin/env python3
"""Deflectly from the command line — train on your own ticket export, ask questions, teach corrections.

No Flask, no server, no API key. Works on a CSV/TSV/JSON export from any helpdesk, or on
JSONL pairs.

    python3 scripts/deflectly_cli.py train tickets.csv
    python3 scripts/deflectly_cli.py ask "how do I rotate an API key?"
    python3 scripts/deflectly_cli.py teach "do you offer nonprofit pricing?" "Yes - 30% off."
    python3 scripts/deflectly_cli.py stats
    python3 scripts/deflectly_cli.py demo        # built-in 56-ticket sample corpus

Installed as a console script (`pip install -e .`) the same commands are just `deflectly ...`.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "app"))

from model import Responder, answer_match, rouge_l  # noqa: E402

DEFAULT_MODEL = Path(".deflectly-model.pkl")
DEFAULT_CORPUS = Path(".deflectly-tickets.jsonl")

# Helpdesk exports name their columns differently. Match on the header, longest first,
# so "ticket_subject" wins over "subject" and we never guess silently.
QUESTION_KEYS = ["question", "subject", "title", "ticket_subject", "issue", "summary", "inquiry", "query"]
ANSWER_KEYS = ["answer", "reply", "response", "resolution", "comment", "body", "description", "solution", "message"]
SENTINEL = object()


def _pick(fieldnames, candidates):
    """First header that matches a candidate key, preferring exact then substring."""
    lowered = {f.lower().strip(): f for f in fieldnames if f}
    for key in candidates:
        if key in lowered:
            return lowered[key]
    for key in candidates:
        for low, original in lowered.items():
            if key in low:
                return original
    return None


def load_pairs(path: Path):
    """Read Q/A pairs from .jsonl, .json, .csv or .tsv. Returns (pairs, note)."""
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="replace")

    if suffix in (".jsonl", ".ndjson"):
        pairs = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            q = obj.get("question") or obj.get("q")
            a = obj.get("answer") or obj.get("a")
            if q and a:
                pairs.append((str(q).strip(), str(a).strip()))
        return pairs, "jsonl"

    if suffix == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("tickets") or data.get("pairs") or list(data.items())
        pairs = []
        for item in data:
            if isinstance(item, dict):
                q = item.get("question") or item.get("subject") or item.get("title")
                a = item.get("answer") or item.get("reply") or item.get("resolution")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                q, a = item[0], item[1]
            else:
                continue
            if q and a:
                pairs.append((str(q).strip(), str(a).strip()))
        return pairs, "json"

    # CSV / TSV (helpdesk exports). Sniff the delimiter rather than trusting the extension.
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "\t" if suffix == ".tsv" else ","
    rows = list(csv.DictReader(text.splitlines(), delimiter=delimiter))
    if not rows:
        return [], "empty"
    q_col = _pick(rows[0].keys(), QUESTION_KEYS)
    a_col = _pick(rows[0].keys(), ANSWER_KEYS)
    if not q_col or not a_col or q_col == a_col:
        raise SystemExit(
            f"Could not find question/answer columns in {path.name}.\n"
            f"  headers seen: {', '.join(rows[0].keys())}\n"
            f"  expected something like a question/subject/title column and an "
            f"answer/reply/resolution column.\n"
            f"  Rename the columns, or pass JSONL of {{\"question\": ..., \"answer\": ...}}."
        )
    pairs = []
    for row in rows:
        q = (row.get(q_col) or "").strip()
        a = (row.get(a_col) or "").strip()
        if q and a:
            pairs.append((q, a))
    return pairs, f"{suffix.lstrip('.') or 'csv'} (question={q_col}, answer={a_col})"


def clean_pairs(pairs, min_chars=8, drop_pii=True):
    """Drop one-liners and (optionally) anything containing customer PII."""
    email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
    phone = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
    kept, dropped = [], {"too_short": 0, "pii": 0}
    for q, a in pairs:
        if len(q) < min_chars or len(a) < min_chars:
            dropped["too_short"] += 1
            continue
        if drop_pii and (email.search(q) or phone.search(q) or email.search(a) or phone.search(a)):
            dropped["pii"] += 1
            continue
        kept.append((q, a))
    return kept, dropped


def save_corpus(pairs, path=DEFAULT_CORPUS):
    with open(path, "w", encoding="utf-8") as fh:
        for q, a in pairs:
            fh.write(json.dumps({"question": q, "answer": a}) + "\n")


def load_corpus(path=DEFAULT_CORPUS):
    if not Path(path).exists():
        raise SystemExit(f"no trained corpus at {path} — run `deflectly train <file>` first")
    pairs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            obj = json.loads(line)
            pairs.append((obj["question"], obj["answer"]))
    return pairs


def load_model_or_explain(path):
    """Responder.load raises FileNotFoundError; the CLI should say what to run instead."""
    if not Path(path).exists():
        raise SystemExit(f"no model at {path} — run `deflectly train <file>` first")
    return Responder.load(path)


def build(pairs):
    t0 = time.time()
    model = Responder("cli", [q for q, _ in pairs], [a for _, a in pairs])
    return model, time.time() - t0


def cmd_train(args):
    if args.file == "demo":
        sys.path.insert(0, str(REPO / "app"))
        from data.sample_data import SAMPLE_TICKETS
        pairs, source = list(SAMPLE_TICKETS), "built-in sample (56 tickets)"
    else:
        path = Path(args.file)
        if not path.exists():
            raise SystemExit(f"no such file: {path}")
        pairs, source = load_pairs(path)

    kept, dropped = clean_pairs(pairs, drop_pii=not args.keep_pii)
    if not kept:
        raise SystemExit("no usable pairs after cleaning — check the columns and --keep-pii")
    if len(kept) < 5:
        print(f"warning: only {len(kept)} pairs. Answers will be close to a lookup table.", file=sys.stderr)

    model, secs = build(kept)
    model.save(args.out)
    save_corpus(kept, args.corpus)
    print(f"trained on {len(kept)} pairs from {source} in {secs:.3f}s")
    print(f"  dropped {dropped['too_short']} too-short, {dropped['pii']} with PII "
          f"({'kept' if args.keep_pii else 'review and re-train with --keep-pii to include'})")
    print(f"  model -> {args.out}   corpus -> {args.corpus}")


def cmd_ask(args):
    model = load_model_or_explain(args.model)
    answer, score, _ = model.predict(args.question)
    if args.json:
        print(json.dumps({"question": args.question, "answer": answer, "confidence": round(score, 4)}))
        return
    print(f"Q: {args.question}")
    print(f"A: {answer}")
    print(f"   confidence {score:.0%}" + ("  (low — teach it or lower your bar)" if score < args.min_confidence else ""))
    if args.topk > 1:
        for rank, (ans, sc) in enumerate(model.predict_topk(args.question, args.topk)[1:], start=2):
            print(f"   alt {rank} ({sc:.0%}): {ans[:90]}")


def cmd_teach(args):
    model = load_model_or_explain(args.model)
    before, before_score, _ = model.predict(args.question)
    pairs = load_corpus(args.corpus)
    pairs = [(q, a) for q, a in pairs if q != args.question] + [(args.question, args.answer)]
    save_corpus(pairs, args.corpus)
    model, secs = build(pairs)
    model.save(args.model)
    _, after_score, _ = model.predict(args.question)
    print(f"taught in {secs:.3f}s on {len(pairs)} pairs")
    print(f"  before ({before_score:.0%}): {before[:80]}")
    print(f"  after  ({after_score:.0%}): {args.answer[:80]}")


def cmd_stats(args):
    pairs = load_corpus(args.corpus)
    qs = {q.lower() for q, _ in pairs}
    words = sum(len(a.split()) for _, a in pairs)
    print(f"corpus:  {len(pairs)} pairs   unique questions: {len(qs)}")
    print(f"answers: {words:,} words, {words/len(pairs):.0f} avg")
    model = load_model_or_explain(args.model)
    vocab = sum(len(getattr(v, "vocabulary_", {})) for v in (model._wq_vec, model._wa_vec, model._ch_vec))
    print(f"model:   {vocab:,} TF-IDF features across 3 channels")


def cmd_demo(args):
    sys.path.insert(0, str(REPO / "app"))
    from data.sample_data import GENERIC_KB, SAMPLE_TICKETS, TEST_QUESTIONS
    store, secs = build(list(SAMPLE_TICKETS))
    baseline, _ = build(list(GENERIC_KB))
    print(f"trained on {len(SAMPLE_TICKETS)} sample tickets in {secs:.3f}s\n")
    print(f"{'question':<46}{'baseline':<22}{'store'}")
    print("-" * 92)
    store_hits = base_hits = 0
    for q, gold in TEST_QUESTIONS:
        base_ans, _, _ = baseline.predict(q)
        store_ans, _, _ = store.predict(q)
        bh = answer_match(base_ans, gold)
        sh = answer_match(store_ans, gold)
        store_hits += sh
        base_hits += bh
        print(f"{q[:44]:<46}{('HIT' if bh else 'miss') + f' ({rouge_l(base_ans, gold):.2f})':<22}"
              f"{('HIT' if sh else 'miss')} ({rouge_l(store_ans, gold):.2f})")
    n = len(TEST_QUESTIONS)
    print(f"\nexact match:  baseline {base_hits}/{n}   store {store_hits}/{n}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="deflectly", description="Train a support answer model on your own tickets.")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("train", help="train from a CSV/TSV/JSON/JSONL export, or 'demo'")
    t.add_argument("file")
    t.add_argument("--out", default=str(DEFAULT_MODEL))
    t.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    t.add_argument("--keep-pii", action="store_true", help="do not drop pairs containing emails/phones")
    t.set_defaults(func=cmd_train)

    a = sub.add_parser("ask", help="answer a question from the trained model")
    a.add_argument("question")
    a.add_argument("--model", default=str(DEFAULT_MODEL))
    a.add_argument("--topk", type=int, default=1)
    a.add_argument("--min-confidence", type=float, default=0.35)
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_ask)

    c = sub.add_parser("teach", help="add a correction and retrain")
    c.add_argument("question")
    c.add_argument("answer")
    c.add_argument("--model", default=str(DEFAULT_MODEL))
    c.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    c.set_defaults(func=cmd_teach)

    s = sub.add_parser("stats", help="what is in the current corpus and model")
    s.add_argument("--model", default=str(DEFAULT_MODEL))
    s.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    s.set_defaults(func=cmd_stats)

    d = sub.add_parser("demo", help="run the built-in sample corpus, offline")
    d.set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
