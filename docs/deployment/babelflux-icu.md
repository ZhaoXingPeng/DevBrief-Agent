# babelflux.icu DevBrief 部署

本目录的 systemd 和 Nginx 模板将 DevBrief 作为独立的回环服务挂载到
`https://www.babelflux.icu/devbrief/`。真实 API key 只放在服务器的
`/etc/devbrief/devbrief.env`，不进入仓库、发布包或日志。

## 运行边界

- DevBrief 监听 `127.0.0.1:8765`，公网请求只经现有 Nginx TLS 入口。
- Nginx `location ^~ /devbrief/` 去掉 URL 前缀后代理到 DevBrief；前端请求会保留该前缀，避免误调用 BabelFlux 的 `/api`。
- SQLite 数据库位于 `/var/lib/devbrief/runs.sqlite3`，不放在发布目录。
- GitHub 写入默认保持 dry-run；只有明确配置完整的生产授权时才允许真实写入。

## 首次安装

1. 创建 `devbrief` 用户、`/opt/devbrief/releases`、`/var/lib/devbrief` 和 root-only `/etc/devbrief/devbrief.env`。
2. 在发布目录创建 Python 3.11 虚拟环境并安装 `pip install .`，再原子更新 `/opt/devbrief/current`。
3. 环境文件至少设置 `DEVBRIEF_BAILIAN_API_KEY` 和 `DEVBRIEF_BAILIAN_BASE_URL`；可选 GitHub 变量按 README 配置。
4. 安装 `deployment/systemd/devbrief.service`，启用并启动服务。
5. 将 `deployment/nginx/devbrief.location.conf` 包含到 `babelflux.icu` 的 TLS server 块，运行 `nginx -t && systemctl reload nginx`。

## 验证与回滚

验证 `systemctl is-active devbrief nginx`、`curl -fsS http://127.0.0.1:8765/health`、
`curl -fsSI https://www.babelflux.icu/devbrief/` 和 `/devbrief/favicon.png`。
回滚时停止服务，将 `current` 指回上一发布目录，启动服务并恢复 Nginx 备份后 reload；不删除 SQLite 数据。
