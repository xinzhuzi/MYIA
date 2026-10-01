"""Stdlib-only smoke tests: they must pass in a dependency-free environment."""

import myia


def test_version():
    assert myia.__version__ == "0.1.0"


def test_pipeline_stages():
    from myia.pipeline import STAGES

    assert STAGES == ["fetch", "classify", "dedup", "analyze", "enrich", "push"]
    assert len(STAGES) == 6


def test_cli_entrypoint_callable():
    from myia.cli import main

    assert callable(main)


def test_dedup_registry_importable():
    from myia.dedup import DedupRegistry

    assert callable(DedupRegistry)


def test_seven_categories():
    from myia.classify.builtin import SEVEN_CATEGORIES

    assert len(SEVEN_CATEGORIES) == 7
