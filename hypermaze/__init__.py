"""hypermaze: the n-dimensional maze, its text game, and helpers for the Colosseum viewer.

    from hypermaze import Maze, Game
    m = Maze(dims=2, size=8, loops=0.0, seed=7)   # same seed -> same maze as the C++ ./maze
    m.to_dict()            # rooms, coords, links (+axis), start, goal, distances, solution
    m.shortest_path()      # start -> Way Out
    g = Game(m, mode="notes"); g.intro(); g.send("Coral Study")
    from hypermaze.replay import load_run   # a run's log -> maze + room path, for replay
"""
from .core import Maze, GOAL_NAME
from .game import Game, MODES

__all__ = ["Maze", "Game", "MODES", "GOAL_NAME"]
