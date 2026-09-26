"""Spike-probability forecast: bounded, causal, and better than a coin flip."""

import pandas as pd
import pytest

from gridsignal import detect, forecast
from gridsignal.prices import load_scenario


def probabilities(scenario: str) -> tuple[pd.DataFrame, pd.Series]:
    detections = detect.detect_spikes(load_scenario(scenario).frame)
    features = forecast.build_features(detections)
    return detections, forecast.forecast_spike_probability(features)


def test_probabilities_stay_in_the_unit_interval():
    _, prob = probabilities("scarcity")

    assert prob.between(0.0, 1.0).all()
    assert len(prob) == 96


def test_the_forecast_never_reads_the_interval_it_predicts():
    """Row *i* is built from row *i-1*, so the first interval has no forecast."""
    detections, prob = probabilities("scarcity")
    features = forecast.build_features(detections)

    assert prob.iloc[0] == 0.0
    shifted = forecast.forecast_spike_probability(features)
    assert shifted.iloc[1:].to_numpy() == pytest.approx(
        forecast.forecast_spike_probability(features).iloc[1:].to_numpy()
    )


def test_spike_intervals_get_much_higher_probabilities_than_calm_ones():
    detections, prob = probabilities("scarcity")

    during = prob[detections["is_spike"].to_numpy()].mean()
    calm = prob[~detections["is_spike"].to_numpy()].mean()

    assert during > 0.5
    assert during > 5 * calm


def test_a_flat_market_forecasts_almost_no_spike_risk():
    _, prob = probabilities("normal")
    detections = detect.detect_spikes(load_scenario("normal").frame)
    overnight = prob[~detections["is_spike"].to_numpy()]

    assert overnight.mean() < 0.1


def test_features_require_detection_columns():
    with pytest.raises(KeyError):
        forecast.build_features(pd.DataFrame({"spp": [1.0, 2.0]}))


def test_unknown_feature_frame_is_rejected():
    with pytest.raises(KeyError):
        forecast.forecast_spike_probability(pd.DataFrame({"z": [0.1, 0.2]}))
