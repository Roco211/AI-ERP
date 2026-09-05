"""Resolve once and pin the allowed address to avoid a second DNS lookup on connect."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from forge_erp.core.errors import Problem
from forge_erp.modules.assistant.domain.providers import ProviderConnection

_PRIVATE_V4 = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_PRIVATE_V6 = ipaddress.ip_network("fc00::/7")
# Cloud metadata endpoints remain forbidden even for a private-model opt-in.
_METADATA = {"100.100.100.200", "168.63.129.16", "fd00:ec2::254", "fd20:ce::254"}


def allowed_address(address: str, private: bool) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if "%" in address:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # IPv6 ::1 is classified as reserved by ipaddress but is a supported local
    # model endpoint. Only this loopback exception is allowed, never unspecified,
    # link-local or multicast addresses.
    if ip.is_loopback:
        return private
    if ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_reserved:
        return False
    if str(ip) in _METADATA:
        return False
    if ip.is_global:
        return True
    if not private:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in block for block in _PRIVATE_V4)
    return ip in _PRIVATE_V6


async def pinned_endpoint(connection: ProviderConnection) -> tuple[str, str, str]:
    parts = urlsplit(connection.endpoint)
    host = parts.hostname
    if not host:
        raise Problem(422, "AI_PROVIDER_ADDRESS", "模型服务地址无效")
    try:
        records = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                host,
                parts.port or (443 if parts.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            ),
            timeout=5,
        )
    except OSError, TimeoutError:
        raise Problem(503, "AI_PROVIDER_DNS", "无法解析模型服务地址") from None
    addresses = list(dict.fromkeys(str(record[4][0]) for record in records))
    if not addresses or any(
        not allowed_address(ip, connection.allow_private_network) for ip in addresses
    ):
        raise Problem(422, "AI_PROVIDER_ADDRESS", "该地址不可访问，请核对本机/内网设置")
    chosen = addresses[0]
    if parts.scheme == "http" and ipaddress.ip_address(chosen).is_global:
        raise Problem(422, "AI_PROVIDER_HTTPS_REQUIRED", "公网模型服务必须使用 HTTPS")
    return str(httpx.URL(connection.endpoint).copy_with(host=chosen)), parts.netloc, host
