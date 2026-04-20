"""Proxy-aware, requests-compatible client utilities.

This module provides a drop-in friendly wrapper around ``requests`` with:
- flexible proxy loading (list, file path, env var, local fallback)
- random proxy rotation
- retry behavior for retryable HTTP/network failures
- a ``Session`` subclass with compatible request methods
"""

from __future__ import annotations

import os
import random
import threading
from typing import Dict, Iterable, Optional

import requests as _requests

Response = _requests.Response
Request = _requests.Request
PreparedRequest = _requests.PreparedRequest
exceptions = _requests.exceptions

DEFAULT_PROXY_FILE = os.getenv("REQUESTSPRO_PROXY_FILE", "proxy.txt")
MAX_PROXY_FAILS = 2
RETRYABLE_STATUS_CODES = {407, 429, 500, 502, 503, 504}

_proxy_lock = threading.Lock()


def normalize_proxy(line: str) -> Optional[str]:
    """Normalize a single proxy line into a requests-compatible proxy URL."""
    line = str(line).strip()
    if not line:
        return None

    if line.startswith("http://") or line.startswith("https://"):
        return line

    parts = line.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"

    return f"http://{line}"


def _resolve_proxy_file(file_path: Optional[str] = None) -> str:
    """Resolve proxy file path from explicit path, env var, then local fallback."""
    if file_path:
        return file_path

    env_proxy_file = os.getenv("REQUESTSPRO_PROXY_FILE")
    if env_proxy_file:
        return env_proxy_file

    return "proxy.txt"


def _normalize_proxy_iterable(proxy_values: Iterable[str]) -> list[str]:
    proxies: list[str] = []
    for value in proxy_values:
        proxy = normalize_proxy(str(value))
        if proxy:
            proxies.append(proxy)
    return list(dict.fromkeys(proxies))


def load_proxies(
    file_path: Optional[str] = None,
    proxy_list: Optional[Iterable[str]] = None,
) -> list[str]:
    """Load and normalize proxies from a list or file.

    Priority order:
    1. ``proxy_list`` (if provided)
    2. explicit ``file_path``
    3. ``REQUESTSPRO_PROXY_FILE`` environment variable
    4. local ``./proxy.txt``
    """
    if proxy_list is not None:
        return _normalize_proxy_iterable(proxy_list)

    resolved_file_path = _resolve_proxy_file(file_path)
    if not os.path.exists(resolved_file_path):
        return []

    with open(resolved_file_path, "r", encoding="utf-8") as proxy_file:
        return _normalize_proxy_iterable(proxy_file)


def save_proxies(proxies: Iterable[str], file_path: Optional[str] = None) -> None:
    """Save normalized proxies back to ``file_path``."""
    resolved_file_path = _resolve_proxy_file(file_path)
    with open(resolved_file_path, "w", encoding="utf-8") as proxy_file:
        for proxy in proxies:
            proxy_file.write(f"{proxy}\n")


class ProxyPool:
    """Thread-safe in-memory proxy pool with failure tracking."""

    def __init__(
        self,
        file_path: Optional[str] = None,
        max_fails: int = MAX_PROXY_FAILS,
        proxy_list: Optional[Iterable[str]] = None,
    ) -> None:
        self._source_file_path = file_path
        self._source_proxy_list = list(proxy_list) if proxy_list is not None else None
        self.file_path = (
            None if self._source_proxy_list is not None else _resolve_proxy_file(file_path)
        )
        self._persist_file_path = (
            self.file_path
            if self.file_path and self._source_proxy_list is None and os.path.exists(self.file_path)
            else None
        )
        self.max_fails = max_fails
        self.proxies = load_proxies(file_path=file_path, proxy_list=self._source_proxy_list)
        self.fail_counts = {proxy: 0 for proxy in self.proxies}
        self.removed_proxies: set[str] = set()

    def reload(self) -> None:
        with _proxy_lock:
            if self._source_proxy_list is not None:
                self.file_path = None
                self._persist_file_path = None
            else:
                self.file_path = _resolve_proxy_file(self._source_file_path)
                self._persist_file_path = (
                    self.file_path if self.file_path and os.path.exists(self.file_path) else None
                )

            self.proxies = load_proxies(
                file_path=self._source_file_path,
                proxy_list=self._source_proxy_list,
            )
            self.fail_counts = {proxy: 0 for proxy in self.proxies}
            self.removed_proxies = set()

    def get_random_proxy(self, exclude: Optional[Iterable[str]] = None) -> Optional[str]:
        with _proxy_lock:
            excluded = set(exclude or [])
            choices = [proxy for proxy in self.proxies if proxy not in excluded]
            if not choices:
                return None
            return random.choice(choices)

    @staticmethod
    def build_proxy_dict(proxy: Optional[str] = None) -> Optional[Dict[str, str]]:
        if not proxy:
            return None
        return {"http": proxy, "https": proxy}

    def mark_success(self, proxy: Optional[str]) -> None:
        if not proxy:
            return
        with _proxy_lock:
            if proxy in self.fail_counts:
                self.fail_counts[proxy] = 0

    def mark_failure(self, proxy: Optional[str]) -> bool:
        if not proxy:
            return False

        with _proxy_lock:
            if proxy not in self.fail_counts:
                self.fail_counts[proxy] = 0

            self.fail_counts[proxy] += 1
            if self.fail_counts[proxy] >= self.max_fails:
                self._remove_proxy_locked(proxy)
                return True
        return False

    def _remove_proxy_locked(self, proxy: str) -> None:
        if proxy in self.proxies:
            self.proxies.remove(proxy)
        if proxy in self.fail_counts:
            del self.fail_counts[proxy]
        self.removed_proxies.add(proxy)
        if self._persist_file_path:
            save_proxies(self.proxies, self._persist_file_path)

    def remove_proxy(self, proxy: Optional[str]) -> None:
        if not proxy:
            return
        with _proxy_lock:
            self._remove_proxy_locked(proxy)

    def get_stats(self) -> dict[str, object]:
        with _proxy_lock:
            return {
                "alive": len(self.proxies),
                "removed": len(self.removed_proxies),
                "fail_counts": dict(self.fail_counts),
            }


_PROXY_POOL = ProxyPool()


def reload_proxies(
    file_path: Optional[str] = None,
    proxy_list: Optional[Iterable[str]] = None,
) -> list[str]:
    """Reload the module-level proxy pool from disk."""
    global _PROXY_POOL
    _PROXY_POOL = ProxyPool(file_path=file_path, proxy_list=proxy_list)
    return _PROXY_POOL.proxies


def get_random_proxy() -> Optional[str]:
    """Return a random proxy from the module-level pool."""
    return _PROXY_POOL.get_random_proxy()


def build_proxy_dict(proxy: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Build a requests ``proxies`` mapping from a proxy URL."""
    proxy = proxy or get_random_proxy()
    return _PROXY_POOL.build_proxy_dict(proxy)


def _is_retryable_exception(exc: Exception) -> bool:
    retryable = (
        _requests.exceptions.ProxyError,
        _requests.exceptions.ConnectTimeout,
        _requests.exceptions.ReadTimeout,
        _requests.exceptions.ConnectionError,
        _requests.exceptions.SSLError,
    )
    return isinstance(exc, retryable)


def _do_request_with_proxy_rotation(
    requester,
    method: str,
    url: str,
    proxy_pool: ProxyPool,
    fixed_proxy: Optional[str] = None,
    rotate_on_each_request: bool = True,
    max_attempts: Optional[int] = None,
    **kwargs,
):
    if kwargs.get("proxies"):
        return requester(method, url, **kwargs)

    attempted: set[str] = set()
    last_exception: Optional[Exception] = None

    available_count = len(proxy_pool.proxies)
    if max_attempts is None:
        max_attempts = max(1, available_count) if available_count else 1

    for _ in range(max_attempts):
        if fixed_proxy and not rotate_on_each_request:
            proxy = fixed_proxy
            if proxy in attempted:
                break
        else:
            proxy = proxy_pool.get_random_proxy(exclude=attempted)

        if not proxy:
            break

        attempted.add(proxy)
        req_kwargs = kwargs.copy()
        req_kwargs["proxies"] = proxy_pool.build_proxy_dict(proxy)

        try:
            response = requester(method, url, **req_kwargs)
            if response.status_code in RETRYABLE_STATUS_CODES:
                proxy_pool.mark_failure(proxy)
                continue
            proxy_pool.mark_success(proxy)
            return response
        except Exception as exc:
            last_exception = exc
            if _is_retryable_exception(exc):
                proxy_pool.mark_failure(proxy)
                continue
            raise

    if last_exception:
        raise last_exception

    # No proxy available or all exhausted: preserve requests behavior.
    return requester(method, url, **kwargs)


class Session(_requests.Session):
    """`requests.Session`-compatible session with proxy pool behavior."""

    def __init__(
        self,
        proxy: Optional[str] = None,
        proxy_file: Optional[str] = None,
        rotate_on_each_request: bool = False,
        max_proxy_fails: int = MAX_PROXY_FAILS,
        proxy_list: Optional[Iterable[str]] = None,
    ) -> None:
        super().__init__()
        self._rotate_on_each_request = rotate_on_each_request
        self._proxy_file = proxy_file
        self._proxy_list = list(proxy_list) if proxy_list is not None else None
        self._proxy_pool = ProxyPool(
            file_path=proxy_file,
            proxy_list=self._proxy_list,
            max_fails=max_proxy_fails,
        )

        normalized_proxy = normalize_proxy(proxy) if proxy else None
        self._fixed_proxy = normalized_proxy or (
            random.choice(self._proxy_pool.proxies) if self._proxy_pool.proxies else None
        )

        if self._fixed_proxy and not self._rotate_on_each_request:
            self.proxies.update({"http": self._fixed_proxy, "https": self._fixed_proxy})

    def request(self, method: str, url: str, **kwargs):
        return _do_request_with_proxy_rotation(
            requester=super().request,
            method=method,
            url=url,
            proxy_pool=self._proxy_pool,
            fixed_proxy=self._fixed_proxy,
            rotate_on_each_request=self._rotate_on_each_request,
            **kwargs,
        )

    def get_stats(self) -> dict[str, object]:
        return self._proxy_pool.get_stats()

    def reload_proxies(self) -> None:
        self._proxy_pool.reload()


def request(method: str, url: str, **kwargs):
    return _do_request_with_proxy_rotation(
        requester=_requests.request,
        method=method,
        url=url,
        proxy_pool=_PROXY_POOL,
        rotate_on_each_request=True,
        **kwargs,
    )


def get(url: str, params=None, **kwargs):
    return request("GET", url, params=params, **kwargs)


def post(url: str, data=None, json=None, **kwargs):
    return request("POST", url, data=data, json=json, **kwargs)


def put(url: str, data=None, **kwargs):
    return request("PUT", url, data=data, **kwargs)


def delete(url: str, **kwargs):
    return request("DELETE", url, **kwargs)


def patch(url: str, data=None, **kwargs):
    return request("PATCH", url, data=data, **kwargs)


def head(url: str, **kwargs):
    return request("HEAD", url, **kwargs)


def options(url: str, **kwargs):
    return request("OPTIONS", url, **kwargs)
