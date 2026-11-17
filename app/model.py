"""Deflectly retrieval responder: REAL trained models, no canned answers.

- Baseline: TF-IDF retrieval fit on a generic e-commerce KB (what an
  out-of-the-box support bot knows without your store's data).
- Store model: trained in-session on the store's own pasted tickets.
  Architecture (all fit at train time on store data):
    * three retrieval channels: question-side TF-IDF (word 1-2 grams),
      answer-side TF-IDF, character 3-5 gram fuzzy TF-IDF
    * query expansion driven by a generic synonym map applied to BOTH the
      store model and the baseline at predict time (fair comparison)
    * score fusion: max over channels

Every score shown in the UI is a real cosine similarity or ROUGE-L computed
at eval time against the held-out gold answers.
"""
import re
import pickle
from dataclasses import dataclass, field
from typing import Union
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Generic English synonym map (same for every store, applied to baseline too).
SYNONYMS = {
    "arrive": "delivery shipping arrive",
    "delivery": "shipping delivery arrive",
    "shipped": "shipping ship arrive",
    "package": "order package tracking parcel",
    "parcel": "order package tracking parcel",
    "mail": "shipping mail package tracking",
    "refund": "refund return money back",
    "money back": "refund return money back",
    "send back": "return send back exchange",
    "returning": "return send back exchange",
    "size": "size fit sizing",
    "fit": "size fit sizing",
    "fits": "size fit sizing",
    "fitting": "size fit sizing",
    "coupon": "discount code coupon promo",
    "promo": "discount code coupon promo",
    "voucher": "discount code coupon promo",
    "damaged": "damaged broken defect",
    "broke": "damaged broken defect",
    "lost": "missing lost never showed",
    "missing": "missing lost",
    "never showed": "missing lost never showed",
    "not here": "missing lost never showed",
    "not arrived": "missing lost never showed",
    "how many days": "how long time days",
    "how many": "how many how much count",
    "days": "days business day time",
    "how long": "how long time days",
    "warm": "warm insulation winter cold",
    "waterproof": "waterproof rain wet weatherproof",
    "wash": "wash care cleaning laundry",
    "installments": "installments payments split",
    "installment": "installments payments split",
    "points": "points loyalty rewards",
    "wore": "worn worn wear used",
    "reject": "reject declined invalid not work",
    "rejected": "reject declined invalid not work",
    "doesn't work": "reject declined invalid not work",
    "what gives": "why problem help",
}


def expand(text: str) -> str:
    """Append synonym expansions for words present in the text (query AND train side)."""
    n = _norm(text)
    extra = []
    for k, v in SYNONYMS.items():
        if re.search(r"\b" + re.escape(k) + r"\b", n):
            extra.append(v)
    return n + " " + " ".join(extra)


@dataclass
class Responder:
    """Trained TF-IDF multi-channel nearest-answer classifier."""
    name: str
    questions: list
    answers: list
    _wq_vec: object = field(init=False, default=None, repr=False)
    _wq: object = field(init=False, default=None, repr=False)
    _wa_vec: object = field(init=False, default=None, repr=False)
    _wa: object = field(init=False, default=None, repr=False)
    _ch_vec: object = field(init=False, default=None, repr=False)
    _ch: object = field(init=False, default=None, repr=False)

    def train(self):
        qs = [expand(q) for q in self.questions]
        ans = [expand(a) for a in self.answers]

        self._wq_vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True,
                                       stop_words="english", min_df=1)
        self._wq = normalize(self._wq_vec.fit_transform(qs))

        self._wa_vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True,
                                       stop_words="english", min_df=1)
        self._wa = normalize(self._wa_vec.fit_transform(ans))

        self._ch_vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                       sublinear_tf=True, min_df=1, max_features=60000)
        self._ch = normalize(self._ch_vec.fit_transform(qs + [" " + _norm(a) + " " for a in self.answers]))
        # char channel spans questions+answers: first half rows = questions, second = answers
        return self

    def __post_init__(self):
        self.train()

    def _scores(self, question: str):
        e = expand(question)
        qv = normalize(self._wq_vec.transform([e]))
        av = normalize(self._wa_vec.transform([e]))
        cv = normalize(self._ch_vec.transform([e]))
        s_q = (qv @ self._wq.T).toarray()[0]
        s_a = (av @ self._wa.T).toarray()[0]
        s_c = (cv @ self._ch.T).toarray()[0]
        n_q = len(self.questions)
        # char channel: [0:n_q] questions, [n_q:2n_q] answers
        s_c_q, s_c_a = s_c[:n_q], s_c[n_q:2 * n_q]
        # fuse: word-question and char-question agree -> strong; answer channels weaker weight
        fused = 0.55 * s_q + 0.25 * s_c_q + 0.10 * s_a + 0.10 * s_c_a
        return fused

    def predict(self, question: str):
        fused = self._scores(question)
        order = fused.argsort()[::-1]
        top3 = [(self.answers[i], float(fused[i])) for i in order[:3]]
        return top3[0][0], top3[0][1], top3

    def predict_topk(self, question: str, k: int = 3):
        _, _, top3 = self.predict(question)
        return top3[:k]

    def save(self, path: Union[str, Path]):
        """Persist the trained responder (vectorizers + matrices) to disk."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: Union[str, Path]):
        """Load a trained responder from disk."""
        with open(path, "rb") as f:
            return pickle.load(f)


def answer_match(pred: str, gold: str) -> int:
    """Exact answer match after normalization."""
    return int(_norm(pred) == _norm(gold))


def rouge_l(pred: str, gold: str) -> float:
    """Word-level ROUGE-L F1 between prediction and gold answer."""
    p, g = _norm(pred).split(), _norm(gold).split()
    if not p or not g:
        return 0.0
    m, n = len(p), len(g)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m):
        for j in range(n):
            dp[i + 1][j + 1] = dp[i][j] + 1 if p[i] == g[j] else max(dp[i][j + 1], dp[i + 1][j])
    lcs = dp[m][n]
    if lcs == 0:
        return 0.0
    prec, rec = lcs / m, lcs / n
    return 2 * prec * rec / (prec + rec)
