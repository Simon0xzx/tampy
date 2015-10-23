"""
Generic classes to interface with the different planning modules used in pma
"""

class Solver(object):

    def __init__(self):
        raise NotImplemented

    def self(self, problem):
        raise NotImplemented

class AbsSolver(Solver):
    
    """
    Should be subclassed in domain spec
    """

    def translate(self, prob):
        """
        return an abstract problem
        """
        raise NotImplemented

class LLSolver(Solver):

    """
    wrapper to deal with solving the full representation
    """
    pass

def parse_solvers(domain_file, problem_file):
    raise NotImplemented
