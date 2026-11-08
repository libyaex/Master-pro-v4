# ملف شاهد تشخيصي — عزل سبب فشل الجمع (collection)
import numpy as np
import pytest


def test_numpy_ok():
    assert np.array([1, 2]).sum() == 3
