import http.server
import urllib.request
import urllib.error
import shutil
import os
import sys

API_BASE = "http://127.0.0.1:8000"
FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(FRONTEND_DIR)

class ProxyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def do_proxy(self):
        url = f"{API_BASE}{self.path}"
        req = urllib.request.Request(url, method=self.command)
        
        # Forward headers
        for key, value in self.headers.items():
            if key.lower() not in ['host', 'accept-encoding']:
                req.add_header(key, value)
                
        # Forward body if POST
        if self.command == 'POST':
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                body = self.rfile.read(content_length)
                req.data = body

        try:
            with urllib.request.urlopen(req) as response:
                self.send_response(response.status)
                for key, value in response.headers.items():
                    # don't forward chunked transfer encoding, length might change or not handled well by simple proxy
                    if key.lower() not in ['transfer-encoding']:
                        self.send_header(key, value)
                self.end_headers()
                shutil.copyfileobj(response, self.wfile)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for key, value in e.headers.items():
                if key.lower() not in ['transfer-encoding']:
                    self.send_header(key, value)
            self.end_headers()
            shutil.copyfileobj(e, self.wfile)
        except urllib.error.URLError as e:
            self.send_error(502, f"Bad Gateway: {e.reason}")
        except Exception as e:
            self.send_error(500, f"Internal Proxy Error: {e}")

    def do_GET(self):
        if self.path.startswith(('/health', '/rankings', '/gateways', '/docs', '/openapi.json')):
            self.do_proxy()
        elif self.path == '/predictions.csv':
            filepath = os.path.join(PROJECT_ROOT, 'predictions.csv')
            if os.path.exists(filepath):
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv')
                self.send_header('Content-Disposition', 'attachment; filename="predictions.csv"')
                self.end_headers()
                with open(filepath, 'rb') as f:
                    shutil.copyfileobj(f, self.wfile)
            else:
                self.send_error(404, "predictions.csv not found")
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith('/run'):
            self.do_proxy()
        else:
            self.send_error(405, "Method Not Allowed")

if __name__ == '__main__':
    port = 5500
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
        
    server_address = ('127.0.0.1', port)
    
    # Threading prevents blocking requests
    class ThreadingHTTPServer(http.server.ThreadingHTTPServer):
        daemon_threads = True
        
    httpd = ThreadingHTTPServer(server_address, ProxyHTTPRequestHandler)
    print(f"Frontend server running on http://127.0.0.1:{port}")
    print(f"Proxying API requests to {API_BASE}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down frontend server.")
        httpd.server_close()
