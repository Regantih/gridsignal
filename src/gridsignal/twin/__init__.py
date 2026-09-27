"""GridSignal Twin: a world model of ERCOT prices driving a simulated fleet.

* :mod:`~gridsignal.twin.data`: real ERCOT real-time prices, 2021 to 2025, three zones.
* :mod:`~gridsignal.twin.world`: the price world model learned from them.
* :mod:`~gridsignal.twin.validate`: how well it forecasts and imitates real years.
* :mod:`~gridsignal.twin.sim`: the simulated fleet and GridSignal's dispatch rule.
* :mod:`~gridsignal.twin.stress`: recovery policies scored over simulated years.
* :mod:`~gridsignal.twin.planner`: the safe commitment for the Control Room's fleet.

Run ``python -m gridsignal.twin report`` to regenerate ``docs/twin/REPORT.md``.
"""
