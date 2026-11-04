# 跨榜单资源沿用验收

2026-11-02 已按用户授权部署正式网站，版本 `resource-reuse-20261102T083856Z`。本机先前隔离验证与正式验收分别记录如下。

实现：新榜单及已有榜单自动关联较早榜单同作品、同视频的已审核媒体；保留原任务与审核，不复制视频。首页按北京时间截止日期每天只显示最新生成版本。后台全部版本、历史播放链接、原排名快照保留。

新增迁移 `007_reused_media` 依赖工作区已有的 `006_review_workflow`。实际发布应核对线上迁移链与并行改动，备份网页、镜像和数据库后再执行；不能直接用整个工作区覆盖正式站点。

验证：完整 Python 测试 130 项通过；最后缩小人工通过后关联范围、增强 public 引用校验后，相关 41 项复测通过。新增测试在 `test_resource_reuse.py`，覆盖跨日/同日版本、跨批次版本号、榜单生成自动沿用、已有版本幂等补齐、新增集、原视频拒绝/撤销/文件变化、不同作者同名、重复任务取消、活动任务保护、匿名 Range 保存、原快照、整榜导出、public 包依赖及 006 到 007 的实际迁移。

浏览器验收：`resource_reuse_ui_smoke.cjs` 实际访问隔离服务，检查首页每天一条、直接播放/切集、真实附件保存与 SHA-256、旧榜单播放、控制区沿用提示、不重复建任务及 1440/768/390/320px 布局，无脚本错误。证据在 `data/resource-reuse-ui/acceptance.json` 与 `home-*.png`。

原 8001/8002 测试端口已有服务，本轮未停止它们；新增 `run_reuse_ui_server.py` 固定使用 8005，只允许新的专用合成数据库。运行方式：

```powershell
$env:DRAMA_DATABASE_URL='sqlite:///D:/drama-mvp/data/test-reuse-ui-新批次.db'
.\.runtime\Scripts\python.exe tests/prepare_reuse_fixture.py
.\.runtime\Scripts\python.exe tests/run_reuse_ui_server.py
# 在另一个终端设置 DRAMA_PLAYWRIGHT_MODULE（如本机无法直接 require playwright），然后执行：
node tests/resource_reuse_ui_smoke.cjs
```

服务重启会在管理模式下补齐现有版本关联；公开请求不会写库。数据库升级沿用现有自动备份、在副本验证、逐表保留原字段及外键检查流程。

正式验收：服务器候选130项检查通过，006到007升级及旧业务字段保留在正式副本预演后应用。榜单6三条目录直接沿用原已审核媒体；122个下载任务和122份媒体保留，无重复下载。HTTPS三部实际播放、旧链接、21,900,777字节真实附件摘要、管理页沿用提示及四种宽度通过，无脚本异常。最后全表摘要与切换准备状态一致，完整性/外键和服务心跳正常，原人工任务步骤保留。证据与回退位置见AGENTS.md及部署说明末尾。
