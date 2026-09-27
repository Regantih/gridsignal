"""A learned world model of ERCOT real-time prices.

What it learns, from real history only:

1. **Day shapes.** Each day is 3 zones x 96 prices. Prices are squashed with
   ``asinh(p / SCALE)`` so a $5,000 spike and a -$20 night both sit on one scale,
   then a PCA basis captures the shared shape of a day across all three zones.
2. **Day types.** Days are clustered in PCA space (k-means). Each cluster's spread
   (a Gaussian over its PCA scores) is used to move a generated day away from the
   real day it starts from, so the model draws *new* days, not replays.
3. **Weather-like persistence.** A first-order Markov chain over day types, fitted
   per season, so hot or stressed days come in runs the way they do in Texas.
4. **Interval texture.** What PCA cannot explain (single-interval spikes) rides along
   with the anchor day, and the whole day slides by up to +/-1 hour, so spikes stay
   sharp and do not always land on the same interval.

Everything is numpy; fitting on two years of data takes about a second.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from gridsignal.twin.data import INTERVALS_PER_DAY, PriceDays

SCALE = 20.0
SEASONS: dict[int, int] = {
    12: 0,
    1: 0,
    2: 0,
    3: 1,
    4: 1,
    5: 1,
    6: 2,
    7: 2,
    8: 2,
    9: 2,
    10: 3,
    11: 3,
}
SEASON_NAMES = ("winter", "spring", "summer", "fall")
SCARCITY_FLOOR = 100.0
MAX_SHIFT = 4  # intervals, i.e. one hour either way


def squash(p: np.ndarray) -> np.ndarray:
    return np.arcsinh(p / SCALE)


def unsquash(z: np.ndarray) -> np.ndarray:
    return np.sinh(z) * SCALE


def kmeans(x: np.ndarray, k: int, rng: np.random.Generator, iters: int = 60) -> np.ndarray:
    """Plain k-means++ returning labels. Deterministic for a given ``rng``."""
    n = len(x)
    centers = [x[rng.integers(n)]]
    for _ in range(1, k):
        d2 = np.min([((x - c) ** 2).sum(1) for c in centers], axis=0)
        centers.append(x[rng.choice(n, p=d2 / d2.sum())])
    c = np.array(centers)
    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        labels = ((x[:, None, :] - c[None]) ** 2).sum(-1).argmin(1)
        new = np.array([x[labels == j].mean(0) if (labels == j).any() else c[j] for j in range(k)])
        if np.allclose(new, c):
            break
        c = new
    return labels


@dataclass
class PriceWorldModel:
    components: int = 12
    clusters: int = 16
    seed: int = 7
    half_life: float = 1.0
    novelty: float = 0.35

    def fit(self, days: PriceDays) -> PriceWorldModel:
        rng = np.random.default_rng(self.seed)
        n, zones, t = days.prices.shape
        self.zones = days.zones
        self.shape = (zones, t)
        z = squash(days.prices).reshape(n, zones * t)
        self.mean_ = z.mean(0)
        u, s, vt = np.linalg.svd(z - self.mean_, full_matrices=False)
        self.basis_ = vt[: self.components]
        scores = (z - self.mean_) @ self.basis_.T
        self.explained_ = float((s[: self.components] ** 2).sum() / (s**2).sum())
        self.scores_ = scores
        self.residuals_ = z - self.mean_ - scores @ self.basis_
        self.seasons_ = np.array([SEASONS[m] for m in days.months])

        self.labels_ = kmeans(scores, self.clusters, rng)
        self.cluster_mean_ = np.zeros((self.clusters, self.components))
        self.cluster_chol_ = np.zeros((self.clusters, self.components, self.components))
        for j in range(self.clusters):
            sj = scores[self.labels_ == j]
            self.cluster_mean_[j] = sj.mean(0)
            cov = np.cov(sj.T) if len(sj) > self.components else np.diag(sj.var(0) + 1e-3)
            self.cluster_chol_[j] = np.linalg.cholesky(cov + 1e-6 * np.eye(self.components))

        self.years_ = days.dates.year.to_numpy()
        self.regimes_ = tuple(int(y) for y in np.unique(self.years_))
        self.months_ = days.months
        self.start_, self.trans_, self.month_freq_ = {}, {}, {}
        for y in (None,) + self.regimes_:
            mask = np.ones(n, bool) if y is None else self.years_ == y
            self.start_[y], self.trans_[y] = self._chain(days, mask)
            # How often each day type shows up in each calendar month of this regime.
            # The season chain says what tends to follow what; this keeps August
            # looking like August (Aug 2023 was far scarcer than June 2023).
            freq = np.zeros((13, self.clusters))
            for m in range(1, 13):
                sel = mask & (days.months == m)
                if not sel.any():
                    sel = days.months == m
                freq[m] = np.bincount(self.labels_[sel], minlength=self.clusters) + 0.1
                freq[m] /= freq[m].sum()
            self.month_freq_[y] = freq

        # Year-to-year level risk: how far one year's median price moved from the
        # last. Drawn once per simulated year when ``level_risk`` is on.
        med = np.array([np.median(days.prices[self.years_ == y]) for y in self.regimes_])
        steps = np.diff(np.log(np.maximum(med, 1.0)))
        self.level_sigma_ = float(np.sqrt((steps**2).mean())) if len(steps) else 0.25
        return self

    def _chain(self, days: PriceDays, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Per-season start and transition probabilities over day types.

        Add-half smoothing inside the season's observed clusters, so the chain never
        jumps to a type that season never saw.
        """
        seasons = np.array([SEASONS[m] for m in days.months])
        trans = np.zeros((4, self.clusters, self.clusters))
        start = np.zeros((4, self.clusters))
        consecutive = np.diff(days.dates.values).astype("timedelta64[D]").astype(int) == 1
        for s_ in range(4):
            sel = mask & (seasons == s_)
            if not sel.any():
                sel = seasons == s_
            seen = np.bincount(self.labels_[sel], minlength=self.clusters)
            start[s_] = seen / max(seen.sum(), 1)
            counts = (seen[None, :] > 0) * 0.5 + np.zeros((self.clusters, self.clusters))
            for i in np.where(consecutive & sel[:-1] & sel[1:])[0]:
                counts[self.labels_[i], self.labels_[i + 1]] += 1
            rows = counts.sum(1, keepdims=True)
            trans[s_] = np.where(rows > 0, counts / np.where(rows > 0, rows, 1), start[s_])
        return start, trans

    def sample(
        self,
        months: np.ndarray,
        rng: np.random.Generator,
        regime: int | str | None = "draw",
        level_risk: bool = True,
        scarcity: float = 1.0,
    ) -> np.ndarray:
        """Generate one simulated day per entry in ``months``, as a consecutive run.

        ``regime`` picks whose day-type mix drives the run: a training year (e.g.
        ``2023``), ``None`` for all years pooled, ``"draw"`` to draw one past year
        uniformly, or ``"recent"`` to draw one with recency weights (half-life
        ``half_life`` years).
        ``level_risk`` also scales the whole run by a lognormal level shock sized
        from real year-to-year moves in the median price.

        ``scarcity`` scales every price above ``SCARCITY_FLOOR``: 1.0 keeps history's
        spikes, 0.5 halves the part of each spike above $100. It is a scenario knob,
        not a forecast: ERCOT's spikes have been shrinking as grid batteries grow, and
        no model fitted to past years predicted how fast (see ``validate``).

        Returns prices in $/MWh with shape ``(len(months), zones, 96)``.
        """
        if scarcity < 0:
            raise ValueError("scarcity must be >= 0")
        n = len(months)
        zones, t = self.shape
        if regime == "draw":
            regime = self.regimes_[int(rng.integers(len(self.regimes_)))]
        elif regime == "latest":
            regime = max(self.regimes_)
        elif regime == "recent":
            # Recency-weighted draw: each year back counts half as much. ERCOT's
            # price behaviour moves year to year (storage build-out, solar, gas), so
            # the latest year is the best single guess but not the only possibility.
            ages = max(self.regimes_) - np.array(self.regimes_)
            w = 0.5 ** (ages / self.half_life)
            regime = self.regimes_[int(rng.choice(len(w), p=w / w.sum()))]
        if regime is not None and regime not in self.start_:
            raise ValueError(f"unknown regime {regime!r}; known: {self.regimes_}")
        trans, freq = self.trans_[regime], self.month_freq_[regime]
        self.last_regime_ = regime
        labels = np.zeros(n, dtype=int)
        labels[0] = rng.choice(self.clusters, p=freq[int(months[0])])
        for i in range(1, n):
            m, s_ = int(months[i]), SEASONS[int(months[i])]
            # Persistence from the season's chain, weighted by how common each type
            # is in this month: p(type | yesterday, month) ~ chain x month mix.
            p = trans[s_, labels[i - 1]] * freq[m]
            if s_ != SEASONS[int(months[i - 1])] or p.sum() == 0:
                p = freq[m]
            labels[i] = rng.choice(self.clusters, p=p / p.sum())

        # Each simulated day starts from a real anchor day of the chosen type (same
        # regime and season when there is one), then moves away from it: its PCA
        # shape is jittered by ``novelty`` x the type's own spread, and the whole day
        # slides by up to an hour. Anchoring keeps real spike structure (a pure
        # Gaussian draw produced about half the real number of spike days);
        # jittering means no simulated day is a copy of a real one.
        eps = rng.standard_normal((n, self.components))
        jitter = np.einsum("nij,nj->ni", self.cluster_chol_[labels], eps) * self.novelty
        z = np.empty((n, zones * t))
        seasons_of = self.seasons_
        for i, lab in enumerate(labels):
            pool = np.where(self.labels_ == lab)[0]
            if regime is not None:
                near = pool[
                    (self.years_[pool] == regime) & (seasons_of[pool] == SEASONS[int(months[i])])
                ]
                if len(near):
                    pool = near
            a = rng.choice(pool)
            day = self.mean_ + (self.scores_[a] + jitter[i]) @ self.basis_ + self.residuals_[a]
            shift = int(rng.integers(-MAX_SHIFT, MAX_SHIFT + 1))
            z[i] = np.roll(day.reshape(zones, t), shift, axis=1).reshape(-1)
        self.last_labels_ = labels
        prices = unsquash(z).reshape(n, zones, t)
        if level_risk:
            level = float(np.exp(rng.normal(0.0, self.level_sigma_)))
            self.last_level_ = level
            # Scale the ordinary part of the price, not the scarcity adder: a gas
            # move shifts a $30 hour to $40, it does not turn $5,000 into $6,700.
            ordinary = np.minimum(prices, 100.0)
            prices = prices + ordinary * (level - 1.0)
        if scarcity != 1.0:
            over = np.maximum(prices - SCARCITY_FLOOR, 0.0)
            prices = prices - over * (1.0 - scarcity)
        return prices

    def sample_year(self, year_months: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        return self.sample(year_months, rng)


def bootstrap_baseline(
    train: PriceDays, months: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """The honest yardstick: replay a random real training day from the same month."""
    out = np.empty((len(months),) + train.prices.shape[1:])
    for i, m in enumerate(months):
        pool = np.where(train.months == m)[0]
        out[i] = train.prices[rng.choice(pool)]
    return out


__all__ = ["INTERVALS_PER_DAY", "SEASON_NAMES", "PriceWorldModel", "bootstrap_baseline"]
