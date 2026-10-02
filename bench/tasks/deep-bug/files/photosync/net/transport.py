"""The part that really talks to the server. Tests swap it for a fake."""


class Transport:
    def send(self, url, data, timeout):
        """Send bytes to url; give up after timeout seconds. Raises TimeoutError or OSError."""
        import urllib.request
        req = urllib.request.Request(url, data=data, method="PUT")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
