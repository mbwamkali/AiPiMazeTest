"""Example plugin: honest signposts. Load with  ./maze.py --plugin plugins/example_signposts.py

Every 25th move, if the player is standing on a shortest route to the Way Out, a faded
arrow points the right way. Also adds a 'pray' command that does nothing useful.
It is only a template: copy it and change it.
"""

EVERY = 25


def setup(game):
    def on_move(game, frm, to):
        if game.moves % EVERY:
            return
        m = game.m
        nxt = [x for x in m.adj[to] if m.dist[x] == m.dist[to] - 1]
        if nxt:
            game.say(f"Someone has scratched a faded arrow on the wall, pointing toward the {m.names[nxt[0]]}.")

    def pray(game, args):
        return "\nYou pray. Nothing happens, but you feel a little better."

    game.on("move", on_move)
    game.add_command("pray", pray)
