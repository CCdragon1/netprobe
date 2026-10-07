# NetProbe — 网络通信能力探测服务

用这台华东 ECS 测试"不同厂商、不同地域服务器/站点的网络通信能力"，量化大陆云服务器的各类连通性问题（GitHub 慢、Google 不通、高校云封禁厂商 IP 等）。

**定位**：贴合你的双主轴——既是 Linux 练手项目（网络诊断实操），又是一个 24h 轻量网络服务。

---

## 一、它能测什么

| 维度 | 说明 | 探测类型 |
|---|---|---|
| TCP 连通 | 某 IP:端口 能不能连上、握手多快 | `tcp` |
| TLS 握手 | HTTPS 站点 TLS 协商耗时 | `tls` |
| HTTP 响应 | 状态码 + 总耗时 | `http` / `https` |
| DNS 解析 | 指定 DNS 服务器是否可用、响应耗时 | `dns` |
| ICMP 通断 | ping 延迟/丢包 | `icmp` |
| 下载测速 | 某 URL 的实际下载速度 | `speed`（预留） |

**目标分三类**（配置里可自由增删）：

1. **自有节点**（`self`）——你的小主机 / VPS / ECS 之间互测
2. **境外站点**（`abroad`）——GitHub、Google、Cloudflare、Docker Hub，重点看"墙"和跨境质量
3. **国内站点**（`domestic`）——阿里云、腾讯云、百度等，测国内跨地域连通

---

## 二、目录结构

```
netprobe/
├── config/targets.json      # 目标清单（改这里加/减目标）
├── prober/
│   ├── probe.py             # 探测核心库（标准库实现，无第三方依赖）
│   └── run.py               # 定时执行器：探测 + 落库 SQLite
├── webapp/
│   └── app.py               # Flask 看板（单文件）
├── templates/index.html     # 看板前端
└── data/netprobe.db         # SQLite 结果库（自动生成）
```

**依赖**：仅 Flask（看板用）+ 标准库（探测器零依赖）。`pip3 install flask` 即可。

---

## 三、部署步骤（在 ECS 上）

### 1. 上传代码

```bash
# 本机执行，把整个 netprobe 目录传到 ECS
scp -r netprobe root@你的ECS公网IP:/opt/
```

### 2. 配置目标清单

编辑 `/opt/netprobe/config/targets.json`，把占位 IP 换成你的真实地址：

```jsonc
{
  "id": "self-home",
  "host": "你家小主机的frp映射地址或IP",
  "port": 7000,
  "enabled": true     // 从 false 改成 true
}
```

要点：
- **自有节点**：小主机填 frp 映射的公网地址+端口；VPS 填公网 IP + 22（SSH 端口最稳）
- **高校云封禁场景**：把被怀疑封禁的目标 IP 加进来，观察通断
- 加新目标就照现有格式加一个对象，改 `enabled` 控制开关

### 3. 装依赖 + 跑一次验证

```bash
cd /opt/netprobe
pip3 install flask
python3 prober/run.py     # 跑一轮，看命令行输出，确认能出结果
```

### 4. 配置定时探测（systemd-timer，每 10 分钟）

创建 `/etc/systemd/system/netprobe.service`：

```ini
[Unit]
Description=NetProbe run once
[Service]
WorkingDirectory=/opt/netprobe
ExecStart=/usr/bin/python3 /opt/netprobe/prober/run.py
```

创建 `/etc/systemd/system/netprobe.timer`：

```ini
[Unit]
Description=NetProbe periodic
[Timer]
OnCalendar=*:0/10
Persistent=true
[Install]
WantedBy=timers.target
```

启用：

```bash
systemctl daemon-reload
systemctl enable --now netprobe.timer
systemctl list-timers | grep netprobe   # 确认定时已生效
```

### 5. 启动看板（常驻）

创建 `/etc/systemd/system/netprobe-web.service`：

```ini
[Unit]
Description=NetProbe web dashboard
After=network.target
[Service]
WorkingDirectory=/opt/netprobe
ExecStart=/usr/bin/python3 /opt/netprobe/webapp/app.py
Environment=PORT=8088
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now netprobe-web
```

### 6. 放行端口 + 访问

- 阿里云控制台安全组放行 `8088`（建议只对你自己的 IP 开放，看板有敏感拓扑信息）
- 浏览器打开 `http://你的ECS公网IP:8088`

> 更稳妥的做法：安全组**不**放行 8088，用 SSH 隧道访问看板：
> ```bash
> # 本机执行
> ssh -L 8088:127.0.0.1:8088 root@你的ECS公网IP
> # 然后本地浏览器打开 http://127.0.0.1:8088
> ```

---

## 四、看板使用

- **主页**：三组卡片（自有/境外/国内），绿色=正常、黄色=缓慢(>1.5s)、红色=不通
- **点卡片**：查看该目标的历史记录（时间/状态/延迟/HTTP码/错误）
- **自动刷新**：每 60 秒
- **缓慢阈值**：延迟 > 1500ms 标黄，可在 `index.html` 的 `statusOf` 里改

---

## 五、典型结论怎么读

以一次真实探测为例：

| 目标 | 结果 | 解读 |
|---|---|---|
| GitHub | 超时 8s | 大陆直连 GitHub 不通/极慢，需要镜像或走代理 |
| Google | Network unreachable | 被墙，符合预期 |
| Docker Hub | Network unreachable | 需配镜像加速器 |
| Cloudflare | 959ms | 通但慢，跨境链路质量差 |
| 阿里云 | 52ms | 国内同地域极快 |
| 阿里 DNS | 6.3ms | 国内 DNS 极快 |

> 这正是你要的"量化不同厂商/地域通信能力"——同一台 ECS，对境内境外、不同厂商的连通差异一目了然。

---

## 六、进阶扩展方向

1. **三机联动**：把这套也部署到小主机和 VPS，形成全网探测网，互测 + 各自对外测，画一张完整的连通矩阵
2. **增加"高校云封禁"专项**：把被怀疑的站点加进清单，24h 持续观测是否被封、何时恢复
3. **告警**：对关键目标（如 frp、VPS）在连续 N 次失败时推送（后续可接钉钉/飞书机器人）
4. **下载测速**：`probe.py` 已预留 `_download_speed`，可对国内 CDN 源测真实下载速度
5. **可视化趋势**：历史数据积累后可画延迟趋势图（当前看板已存历史，加个 chart 即可）

---

## 七、注意事项

- **探测频率别太高**：10 分钟一轮足够，对目标站保持礼貌，避免被当成扫描
- **只探测自己的节点 + 公开站点**：不扫他人 IP 段，不碰未授权的目标
- **看板含内网拓扑**：8088 别对公网全开放，用隧道或限制 IP
- **内存占用**：探测器按需运行（几秒），看板常驻约 30–50MB，1.6G 内存完全无压力
