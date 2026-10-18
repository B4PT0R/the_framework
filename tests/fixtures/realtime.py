"""Inert live sideband for transport and binding contracts; no backend calls."""

class Sideband:
    def __init__(self):
        self.sent = []

    def send(self, event):
        self.sent.append(event)

    def close(self):
        pass

