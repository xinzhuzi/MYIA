"""Stdlib-only smoke tests: they must pass in a dependency-free environment."""

import shishi


def test_version():
    assert shishi.__version__ == "0.0.1"


def test_pipeline_stages():
    from shishi.pipeline import STAGES

    assert STAGES == ["fetch", "classify", "dedup", "analyze", "enrich", "push"]
    assert len(STAGES) == 6


def test_cli_entrypoint_callable():
    from shishi.cli import main

    assert callable(main)


def test_dedup_registry_importable():
    from shishi.dedup import DedupRegistry

    assert callable(DedupRegistry)


def test_seven_categories():
    from shishi.classify.builtin import SEVEN_CATEGORIES

    assert len(SEVEN_CATEGORIES) == 7
