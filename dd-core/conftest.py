"""Root conftest: load the dd_core testkit pytest plugin for this repo's own
suite, so the `differential` fixture (and, from Phase D, the State-Leak
Detector) are available to tests here. Host projects that vendor dd-core opt in
the same way -- `pytest -p dd_core.testkit.plugin` or their own pytest_plugins.
"""

pytest_plugins = ["dd_core.testkit.plugin"]
