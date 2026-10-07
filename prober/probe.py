#!/usr/bin/env python3
"""
网络探测核心库 —— 支持 ICMP / TCP / TLS / HTTP / DNS / 测速 多维度探测。

设计目标：
  - 依赖少（仅标准库），方便在 2C2G 的轻量 ECS 上直接跑
  - 每个探测带超时，互不阻塞，单轮总耗时可控
  - 结果结构化，便于落库和看板展示
"""
import socket
import ssl
import time
import json
import re
import struct
import subprocess
import os
from datetime import datetime, timezone


def load_json_with_comments(path):
    """加载支持 // 注释的 JSON 配置文件。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # 去掉 // 或 # 行注释（不处理字符串内，配置里没有这种场景）
    text = re.sub(r"^\s*(?:#|//).*$", "", text, flags=re.MULTILINE)
    return json.loads(text)

# ---------- 底层工具 ----------

def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tcp_connect(host, port, timeout=5):
    """返回 (是否连通, 握手耗时ms)。失败返回 (False, None)。"""
    t0 = time.time()
    try:
        addrinfos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, None, "DNS 解析失败"
    last_err = "连接失败"
    for af, socktype, proto, _, sa in addrinfos:
        s = socket.socket(af, socktype, proto)
        s.settimeout(timeout)
        try:
            s.connect(sa)
            ms = (time.time() - t0) * 1000
            s.close()
            return True, round(ms, 1), None
        except socket.timeout:
            last_err = "连接超时"
        except OSError as e:
            last_err = f"连接失败: {e.errno}"
        finally:
            s.close()
    return False, None, last_err


def _tls_handshake(host, port=443, timeout=5):
    """返回 (是否成功, TLS握手耗时ms, 错误)。"""
    t0 = time.time()
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                ms = (time.time() - t0) * 1000
                return True, round(ms, 1), None
    except Exception as e:
        return False, None, str(e)


def _http_request(host, port=443, path="/", timeout=8, use_tls=True):
    """返回 (HTTP状态码, 总耗时ms, 错误)。"""
    t0 = time.time()
    try:
        ctx = ssl.create_default_context() if use_tls else None
        raw = socket.create_connection((host, port), timeout=timeout)
        if use_tls:
            raw = ctx.wrap_socket(raw, server_hostname=host)
        req = (f"GET {path} HTTP/1.1\r\n"
               f"Host: {host}\r\n"
               f"User-Agent: netprobe/1.0\r\n"
               f"Connection: close\r\n\r\n").encode()
        raw.sendall(req)
        raw.settimeout(timeout)
        data = b""
        while True:
            try:
                chunk = raw.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
            if len(data) > 8192:  # 只看响应头，够了就停
                break
        raw.close()
        ms = (time.time() - t0) * 1000
        first_line = data.split(b"\r\n", 1)[0].decode(errors="ignore")
        if first_line.startswith("HTTP/"):
            code = int(first_line.split()[1])
            return code, round(ms, 1), None
        return 0, round(ms, 1), "非 HTTP 响应"
    except Exception as e:
        return None, round((time.time() - t0) * 1000, 1), str(e)


def _icmp_ping(host, count=3, timeout=2):
    """用系统 ping 命令测延迟/丢包（需 root 或 CAP_NET_RAW）。返回 (可达, 平均RTT, 丢包率, 错误)。"""
    try:
        out = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout), host],
            capture_output=True, text=True, timeout=count * (timeout + 1) + 2
        )
        if out.returncode != 0:
            return False, None, 100.0, "ping 失败（可能被禁 ICMP 或无权限）"
        # 解析 "rtt min/avg/max/mdev = 1.2/3.4/5.6/0.7 ms"
        rtt = None
        loss = 0.0
        for line in out.stdout.splitlines():
            if "packet loss" in line:
                try:
                    loss = float(line.split("%")[0].split()[-1])
                except Exception:
                    pass
            if "rtt min/avg/max" in line or "min/avg/max" in line:
                try:
                    rtt = float(line.split("=")[1].split("/")[1])
                except Exception:
                    pass
        return True, rtt, loss, None
    except subprocess.TimeoutExpired:
        return False, None, 100.0, "ping 超时"
    except FileNotFoundError:
        return False, None, 100.0, "系统无 ping 命令"


def _dns_query(server, qname="example.com", timeout=5):
    """向指定 DNS 服务器查询，测该 DNS 是否可用及响应耗时。返回 (可用, 耗时ms, 错误)。"""
    t0 = time.time()
    # 构造一个简单的 A 记录查询报文
    tid = os.urandom(2)
    flags = 0x0100  # 标准查询
    qdcount = 1
    header = struct.pack(">HHHHHH", int.from_bytes(tid, "big"), flags, qdcount, 0, 0, 0)
    qname_bytes = b""
    for label in qname.split("."):
        qname_bytes += bytes([len(label)]) + label.encode()
    qname_bytes += b"\x00"
    question = qname_bytes + struct.pack(">HH", 1, 1)  # A, IN
    packet = header + question
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(packet, (server, 53))
        data, _ = s.recvfrom(512)
        s.close()
        ms = (time.time() - t0) * 1000
        return True, round(ms, 1), None
    except Exception as e:
        return False, None, str(e)


def _download_speed(host, port=443, path="/", timeout=10, use_tls=True):
    """下载测速：在超时窗口内尽量下载，返回 KB/s。注意 3M 带宽下结果受限于本机带宽。"""
    t0 = time.time()
    try:
        ctx = ssl.create_default_context() if use_tls else None
        raw = socket.create_connection((host, port), timeout=timeout)
        if use_tls:
            raw = ctx.wrap_socket(raw, server_hostname=host)
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
               f"User-Agent: netprobe-speed/1.0\r\nConnection: close\r\n\r\n").encode()
        raw.sendall(req)
        raw.settimeout(timeout)
        total = 0
        while True:
            try:
                chunk = raw.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            total += len(chunk)
            if time.time() - t0 > timeout:
                break
        raw.close()
        elapsed = max(time.time() - t0, 0.001)
        return round(total / 1024 / elapsed, 1), None
    except Exception as e:
        return None, str(e)


# ---------- 探测调度 ----------

def probe_target(t: dict) -> dict:
    """对单个目标执行探测，返回结构化结果。"""
    result = {
        "id": t["id"],
        "name": t["name"],
        "group": t.get("group", "unknown"),
        "type": t["type"],
        "host": t["host"],
        "port": t.get("port"),
        "time": _now_iso(),
        "ok": False,
        "metric": None,      # 主要度量：延迟ms 或 码
        "metric_label": "",  # 度量标签
        "extra": {},
        "error": None,
    }
    typ = t["type"]
    host = t["host"]
    port = t.get("port", 443)
    path = t.get("path", "/")

    if typ == "icmp":
        ok, rtt, loss, err = _icmp_ping(host)
        result["ok"] = ok
        result["metric"] = rtt
        result["metric_label"] = "avg_rtt_ms"
        result["extra"]["loss"] = loss
        result["error"] = err
    elif typ == "tcp":
        ok, ms, err = _tcp_connect(host, port)
        result["ok"] = ok
        result["metric"] = ms
        result["metric_label"] = "tcp_ms"
        result["error"] = err
    elif typ == "tls":
        ok, ms, err = _tls_handshake(host, port)
        result["ok"] = ok
        result["metric"] = ms
        result["metric_label"] = "tls_ms"
        result["error"] = err
    elif typ in ("http", "https"):
        use_tls = typ == "https" or port == 443
        code, ms, err = _http_request(host, port, path, use_tls=use_tls)
        result["ok"] = code is not None and 200 <= code < 500
        result["metric"] = ms
        result["metric_label"] = "http_ms"
        result["extra"]["http_code"] = code
        result["error"] = err
    elif typ == "dns":
        ok, ms, err = _dns_query(host)
        result["ok"] = ok
        result["metric"] = ms
        result["metric_label"] = "dns_ms"
        result["error"] = err
    else:
        result["error"] = f"未知探测类型: {typ}"
    return result


def run_all(targets: list) -> list:
    """对目标列表逐一探测，返回结果列表。"""
    results = []
    for t in targets:
        if not t.get("enabled", True):
            continue
        r = probe_target(t)
        results.append(r)
        # 简单打印，便于命令行查看
        status = "OK " if r["ok"] else "FAIL"
        metric = r["metric"] if r["metric"] is not None else "-"
        err = f" | {r['error']}" if r["error"] else ""
        print(f"[{status}] {r['group']:8s} {r['name']:20s} "
              f"{r['type']:5s} {r['host']}:{r['port'] or ''} "
              f"=> {metric}{err}")
    return results


if __name__ == "__main__":
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config", "targets.json")
    cfg = load_json_with_comments(cfg_path)
    results = run_all(cfg["targets"])
    print(f"\n共探测 {len(results)} 个目标，成功 {sum(1 for r in results if r['ok'])} 个")
