"""Test-only access to the already-installed TrustMark package model cache."""

from pathlib import Path
import urllib.error

import trustmark

from trustmark_model_manager import TrustMarkFactory, TrustMarkModelManager


def installed_model_factory():
    model_directory = Path(trustmark.__file__).resolve().parent / "models"
    manager = TrustMarkModelManager(
        cache_directory=model_directory,
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("tests must not access the network")
        ),
    )
    return TrustMarkFactory(manager).create()
