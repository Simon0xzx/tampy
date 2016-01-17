from IPython import embed as shell
import numpy as np

class Matrix(object):
    """
    The matrix class is useful for tracking object poses.
    """
    def __init__(self, *args):
        raise NotImplementedError

class Vector2d(Matrix):
    """
    The NAMO domain uses the Vector2d class to track poses of objects in the grid.
    """
    def __init__(self, vec):
        if type(vec) is str:
            if not vec.endswith(")"):
                vec += ")"
            vec = eval(vec)
        self.vec = np.array(vec).reshape((2, 1))
        assert len(self.vec) == 2

    def shape(self):
        return self.vec.shape

    def __getitem__(self, i):
        return Vector2d(self.vec[i])

    def __eq__(self, other):
        return np.array_equal(self.vec, other.vec)

    def __repr__(self):
        return repr(self.vec)