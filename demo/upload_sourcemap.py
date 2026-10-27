"""Upload a source map for a release (stdlib only).

    python demo/upload_sourcemap.py --api http://localhost:8000 --token ADMIN --project 1 \
        --release demo-1.0.0 --file demo/browser/dist/app.min.js.map --name app.min.js
"""
import argparse
import urllib.request
import uuid

p = argparse.ArgumentParser()
p.add_argument("--api", required=True)
p.add_argument("--token", required=True)
p.add_argument("--project", type=int, required=True)
p.add_argument("--release", required=True)
p.add_argument("--file", required=True)
p.add_argument("--name", required=True, help="name of the minified file the stack frames point at")
a = p.parse_args()

b = uuid.uuid4().hex
data = open(a.file, "rb").read()
body = (f'--{b}\r\nContent-Disposition: form-data; name="filename"\r\n\r\n{a.name}\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="map.json"\r\n'
        f'Content-Type: application/json\r\n\r\n').encode() + data + f"\r\n--{b}--\r\n".encode()
req = urllib.request.Request(
    f"{a.api.rstrip('/')}/api/0/projects/{a.project}/releases/{a.release}/sourcemaps", data=body, method="POST",
    headers={"Authorization": f"Bearer {a.token}", "Content-Type": f"multipart/form-data; boundary={b}"})
with urllib.request.urlopen(req) as r:
    print(r.status, r.read().decode())
