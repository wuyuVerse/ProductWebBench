from __future__ import annotations

import argparse
import functools
import http.server
import re
import socketserver
import urllib.parse
from pathlib import Path


class CleanUrlStaticHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        translated = Path(self.translate_path(self.path))
        if translated.is_file() and translated.suffix.lower() in {".html", ".htm", ".xml"}:
            raw = translated.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return super().do_GET()
            host = self.headers.get("Host")
            if host:
                text = re.sub(r"https?://(?:localhost|127\\.0\\.0\\.1):\\d{2,5}", f"http://{host}", text)
                raw = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", self.guess_type(str(translated)))
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        return super().do_GET()

    def translate_path(self, path: str) -> str:
        translated = Path(super().translate_path(path))
        root = Path(self.directory).resolve()
        parsed = urllib.parse.urlparse(path)
        raw_path = urllib.parse.unquote(parsed.path)
        clean = raw_path.strip("/")
        parts = [part for part in clean.split("/") if part]

        if parts:
            prefix_stripped = root.joinpath(*parts[1:]) if len(parts) > 1 else root
            if not translated.exists() and prefix_stripped.exists():
                if prefix_stripped.is_dir():
                    index = prefix_stripped / "index.html"
                    if index.exists():
                        return str(index)
                return str(prefix_stripped)

            if not translated.exists() and len(parts) == 1:
                index = root / "index.html"
                if index.exists():
                    return str(index)

        if translated.is_dir():
            index = translated / "index.html"
            if index.exists():
                return str(index)
            if clean:
                sibling_html = root / f"{clean}.html"
                if sibling_html.exists():
                    return str(sibling_html)

        if not translated.exists() and clean and not clean.endswith(".html"):
            html_path = root / f"{clean}.html"
            if html_path.exists():
                return str(html_path)
            if "." not in Path(clean).name:
                index = root / "index.html"
                if index.exists():
                    return str(index)

        return str(translated)

    def list_directory(self, path: str):
        self.send_error(404, "No directory index")
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", type=int)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--directory", default=".")
    args = parser.parse_args()

    handler = functools.partial(CleanUrlStaticHandler, directory=args.directory)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((args.bind, args.port), handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
