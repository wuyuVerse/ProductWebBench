"""Serve generated Hugo files without SPA fallback or implicit URL prefixes."""
import argparse
import functools
import http.server
from pathlib import Path
import urllib.parse


class HugoStaticHandler(http.server.SimpleHTTPRequestHandler):
    def resolve_file(self):
        root = Path(self.directory).resolve()
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        parts = path.strip('/').split('/') if path.strip('/') else []
        if '\x00' in path or any(p in {'.', '..'} for p in parts):
            return None
        target = root.joinpath(*parts)
        candidates = [target / 'index.html'] if target.is_dir() else [target]
        if not target.exists() and target.suffix == '':
            candidates.append(target.with_suffix('.html'))
        for candidate in candidates:
            if candidate.is_file() and candidate.resolve().is_relative_to(root):
                return candidate
        return None

    def send_head(self):
        target = self.resolve_file()
        status = 200
        if target is None:
            target = Path(self.directory) / '404.html'
            status = 404
            if not target.is_file() or not target.resolve().is_relative_to(Path(self.directory).resolve()):
                self.send_error(404, 'File not found')
                return None
        try:
            stream = target.open('rb')
        except OSError:
            self.send_error(404, 'File not found')
            return None
        self.send_response(status)
        self.send_header('Content-Type', self.guess_type(str(target)))
        self.send_header('Content-Length', str(target.stat().st_size))
        self.end_headers()
        return stream


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('port', type=int)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--directory', default='.')
    args = parser.parse_args()
    handler = functools.partial(HugoStaticHandler, directory=args.directory)
    with http.server.ThreadingHTTPServer((args.bind, args.port), handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
