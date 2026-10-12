"""Database setup: the shared `Base` (base.py), the engine and session factory (engine.py) and the
`get_session` dependency (session.py).

Import from the submodules, not from this package, so that importing `Base` in a model never
creates the engine as a side effect.
"""
