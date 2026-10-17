# XCPC Board · 一起看比赛

可复用的 XCPCIO 观赛看板，无前端构建依赖，后端只需要 Python 3.10+ 标准库。

## 使用

```sh
export ADMIN_TOKEN='replace-with-a-long-random-secret-at-least-20-characters'
python3 server.py
# http://127.0.0.1:8766
```

点右上角「管理赛站」，输入 `ADMIN_TOKEN`：可编辑赛站标题、XCPCIO 榜单链接、B 站房间号、封榜时间，或新增其他赛站。初始西安使用热身赛链接；长春留空等待公布，未猜测或伪造正赛数据。链接支持 `?group=official` 等原榜排名分组。

- 点 ☆ 加入个人关注；「关注分组」创建浏览器本地分组。个人分组和个人备注仅存 localStorage，无需登录。
- 管理员可创建公共分组、指定新访客默认视图，填写所有访客可见的群员备注。个人备注优先于公共备注。
- 切换原榜组会改变官方排名口径；个人/公共关注筛选不会重排名次。
- 分组和备注按榜单路径保存；切换热身/正赛时互不污染，切回旧链接可恢复。
- 每个赛站可填写 B 站房间号或直播间链接。访客按需展开 iframe；如果 B 站限制嵌入，可打开官方直播间。
- 新提交依次滑入右侧队列，判题结果更新也会重新入队。队列显示当前排名、队名、学校、备注、题号、结果、当前 AC 数和提交时间，可暂停并仅看当前分组。首次加载显示近期真实提交，不将历史伪装为直播。

## 数据与缓存

研究依据为 [XCPCIO 公开数据](https://board.xcpcio.com/icpc/51st/xian-warmup?group=official) 和 [上游 core 实现](https://github.com/xcpcio/xcpcio/tree/main/packages/libs/core/src)。服务端直接读取 `config.json`、`team.json`、`run.json` 和配置引用的学校数据，兼容旧 `problem_id` 和新版 `problems`、多语言队名、秒/毫秒时间戳。罚时忽略 CE/PE 等上游不计罚时结果，支持按秒、按题取整到分钟或汇总后取整，按解题数、罚时、最后解题分钟排序，平分共享名次。

后台每 15 秒抓取提交（可通过 `POLL_SECONDS` 调整，最短 10 秒），元信息约每分钟重新校验，支持 ETag/Last-Modified。访客只读取统一缓存，不会按人数放大上游请求。

成功的完整原始快照 gzip 压缩后原子落盘，保留最新和上一次变更快照。网络错误、超时、数据异常或突然空榜会保留成功缓存并标记过期。进程重启会从磁盘恢复，最新文件损坏时尝试前一份。浏览器另外保留最近成功的可展示快照，连接服务失败时继续显示。

默认开赛 4 小时封榜，若上游更早封榜则采用较早时间。封榜后的原始结果在服务端统一替换为 `FROZEN`，榜单与提交 API 都不泄漏隐藏结果。比赛结束后继续保留封榜；管理员确认官方已公开最终结果后可手动解封。上游本身没有公开的结果无法恢复。队列 API 保留最近 800 次提交，隐藏标签页暂停前台轮询。

配置和原始快照位于 `DATA_DIR`（默认 `data/`），不提交到 Git。管理密钥只放环境变量，前端仅在当前标签页 sessionStorage 保存；不要在链接中携带密钥。

## 部署

`deploy/` 提供 systemd、Caddy 域名站点及保留旧 `:8082` 入口的 Nginx 配置。生产目录 `/root/xcpcboard`，服务名 `xcpcboard.service`，后端监听 `127.0.0.1:8765`。

```ini
# /root/xcpcboard/.env (chmod 600)
ADMIN_TOKEN=replace-with-random-secret
BIND=127.0.0.1
PORT=8765
DATA_DIR=/root/xcpcboard/data
POLL_SECONDS=15
```

更新程序文件时保留 `data/` 和 `.env`，然后 `systemctl restart xcpcboard`。DNS 将 `xcpcboard.wannafly.cn` 的 A 记录指向 `43.155.179.39` 后，Caddy 会自动申请 HTTPS 证书。不要把旧版 Nginx 的静态根目录配置继续用于新版，以免暴露非静态文件。

旧邀请赛界面保留在 `/legacy/`，原 Node 服务代码作为历史实现保留在 `legacy/server.js`。线上替换之前需备份原部署文件与代理配置，停止原 Node 进程，使用 systemd 接管。

## 验证

```sh
python3 -m unittest discover -s tests -v
python3 tests/smoke_live.py # 显式联网验证热身赛、封榜过滤和重启恢复
```

`run_preview.py` 仅用于本地开发，密钥是公开的测试值，不能用于公网部署。
