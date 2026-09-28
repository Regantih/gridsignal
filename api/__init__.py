"""GridSignal web API: a thin FastAPI layer over the existing engine.

It wraps :class:`gridsignal.control_room.engine.ControlRoomEngine`, the twin's planner,
risk map, learning loop and live ERCOT feed. It computes nothing of its own: every number
it returns is the engine's, and the React front end in ``web/`` only formats it.
"""
