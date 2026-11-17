#!/usr/bin/env python3
"""Reproduce the README's headline numbers, offline — no server, no API key.

    python3 scripts/benchmark.py

Trains the store model on the built-in 56-ticket sample corpus and scores it
against the generic baseline bot on the 5 held-out questions, using the same
code path the app uses.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from data.sample_data import GENERIC_KB, SAMPLE_TICKETS, TEST_QUESTIONS  # noqa: E402
from model import Responder, answer_match, rouge_l  # noqa: E402


def score(responder, questions):
    exact = 0
    rouge = 0.0
    for q, gold in questions:
        answer, _score, _ = responder.predict(q)
        exact += int(answer_match(answer, gold))
        rouge += rouge_l(answer, gold)
    return exact / len(questions), rouge / len(questions)


def main():
    print(f"corpus: {len(SAMPLE_TICKETS)} ticket pairs   held-out questions: {len(TEST_QUESTIONS)}\n")

    t0 = time.time()
    baseline = Responder("baseline", [q for q, a in GENERIC_KB], [a for q, a in GENERIC_KB])
    t_base = time.time() - t0

    t0 = time.time()
    store = Responder("store", [q for q, a in SAMPLE_TICKETS], [a for q, a in SAMPLE_TICKETS])
    t_store = time.time() - t0

    base_acc, base_rouge = score(baseline, TEST_QUESTIONS)
    store_acc, store_rouge = score(store, TEST_QUESTIONS)

    n = len(TEST_QUESTIONS)
    print(f"{'model':<22}{'exact match':<16}{'ROUGE-L':<12}{'train time'}")
    print("-" * 62)
    print(f"{'generic baseline':<22}{f'{round(base_acc*n)}/{n} ({base_acc:.0%})':<16}{base_rouge:<12.3f}{t_base:.3f}s")
    print(f"{'store-trained':<22}{f'{round(store_acc*n)}/{n} ({store_acc:.0%})':<16}{store_rouge:<12.3f}{t_store:.3f}s")
    print()

    # the correction loop: one wrong answer, one fix, same question re-asked
    # (mirrors POST /api/feedback: the pair is merged into the ticket set and the
    # model is rebuilt, which is the whole trick — no fine-tuning step)
    q = "do you offer a nonprofit discount?"
    answer, before, _ = store.predict(q)
    correction = "Yes, nonprofits get 30% off any plan."
    merged = [(tq, ta) for tq, ta in SAMPLE_TICKETS] + [(q, correction)]
    t0 = time.time()
    store = Responder("store", [tq for tq, _ in merged], [ta for _, ta in merged])
    retrain = time.time() - t0
    _, after, _ = store.predict(q)
    print(f"correction loop, same question: {before:.3f} -> {after:.3f} confidence (retrain {retrain:.3f}s)")
    print(f"  before: {answer[:70]}")
    print(f"  after:  {correction}")

    print()
    print("These are the numbers the README quotes. If a change makes them worse, say so in the PR.")


if __name__ == "__main__":
    main()
