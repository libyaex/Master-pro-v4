# فحص سلامة سريع — يُشغَّل دائماً أولاً
import numpy as np


def test_numpy_ok():
    assert np.array([1, 2]).sum() == 3
