# requests-prox

`requests-prox` is a proxy-aware wrapper around `requests` designed to stay familiar while adding practical proxy rotation behavior.

It is intended as a drop-in style approach for users who want to write:

```python
import requests_prox as requests
```

and keep using the common `requests` API (`get`, `post`, `Session`, etc.) with proxy helpers.

## Features

- Requests-compatible API surface for common usage
- Flexible proxy configuration (list, file, env var, local fallback)
- Proxy normalization and rotation
- Session support with optional fixed proxy or per-request rotation
- Retry behavior for common transient proxy/network failures
- Automatic removal of repeatedly failing proxies from the in-memory/file pool

## Installation

Install from PyPI:

```bash
pip install requests-prox
```

Install directly from GitHub:

```bash
pip install git+https://github.com/its-wajd/requests-prox.git
```

## Quick Usage

```python
import requests_prox as requests

response = requests.get("https://httpbin.org/get", timeout=15)
print(response.status_code)
```

`requests-prox` re-exports these public names:

- `Response`, `Request`, `PreparedRequest`, `exceptions`
- `Session`
- `request`, `get`, `post`, `put`, `delete`, `patch`, `head`, `options`
- `load_proxies`, `save_proxies`, `reload_proxies`, `get_random_proxy`, `build_proxy_dict`

## Proxy Setup (Priority Order)

`requestspro` resolves proxies in this order:

1. Direct proxy list (`proxy_list=[...]`)
2. Explicit proxy file path (`proxy_file="..."`)
3. Environment variable (`REQUESTSPRO_PROXY_FILE`)
4. Local fallback (`./proxy.txt`)
5. No proxies (direct requests behavior)

This design avoids modifying anything inside `site-packages` and keeps user proxy data outside the package source.

## Supported Proxy Formats

One proxy per line (file mode), or one proxy per item (list mode).

- `host:port`
- `host:port:username:password`
- `http://host:port`
- `https://host:port`
- `http://username:password@host:port`

Example:

```text
127.0.0.1:8080
10.0.0.1:3128:user:pass
http://user:pass@192.168.1.20:8000
```

## Environment Variable Usage

Linux/macOS:

```bash
export REQUESTSPRO_PROXY_FILE=/path/to/proxy.txt
```

Windows (cmd):

```cmd
set REQUESTSPRO_PROXY_FILE=C:\path\to\proxy.txt
```

After setting the variable, normal usage automatically loads proxies from that path:

```python
import requests_prox as requests

r = requests.get("https://httpbin.org/ip", timeout=15)
print(r.status_code)
```

## Session Usage

Use an explicit proxy file:

```python
import requests_prox as requests

s = requests.Session(proxy_file="/path/to/proxy.txt")
r = s.get("https://httpbin.org/ip", timeout=15)
print(r.status_code)
```

Use a direct proxy list (highest priority):

```python
import requests_prox as requests

s = requests.Session(
    proxy_list=[
        "http://1.2.3.4:8080",
        "http://user:pass@1.2.3.4:8080",
    ]
)
r = s.get("https://httpbin.org/ip", timeout=15)
print(r.status_code)
```

Rotate on each request:

```python
import requests_prox as requests

s = requests.Session(proxy_file="proxy.txt", rotate_on_each_request=True)
r = s.get("https://httpbin.org/ip", timeout=15)
print(r.status_code)
```

## Notes and Limitations

- This package is synchronous and built on top of `requests`.
- If no proxies are available, requests fall back to direct requests behavior.
- Proxy health/removal logic is simple by design (failure-count based).
- Keep your real proxy credentials out of source control; do not commit local `proxy.txt`.

## Author

Wajd Dev (@its-wajd)
