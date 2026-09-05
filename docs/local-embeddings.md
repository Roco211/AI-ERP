# 本地商品语义搜索

用户已选择在自己的电脑或服务器运行。使用 Ollama + BGE-M3（1024 维、多语言、MIT 许可）。模型约 1.2 GB，Ollama 运行环境镜像另占磁盘。无云端 API 密钥或按调用量收费；需要本机 CPU、内存、电力与首次下载流量。初始 Compose 限制模型容器最多使用 4 个 CPU / 4 GB 内存。

## 安装与运行

从仓库根目录，先确保 Bootstrap 的 `.env`、数据库和 Redis 已建立：

```sh
make install migrate
make semantic-infra      # 本机可选模型容器，端口 127.0.0.1:11438
make semantic-pull       # 首次下载 bge-m3:567m
make semantic-enable     # 检查下载结果，将模型指纹写入本地 .env
make semantic-rebuild    # 为 DEMO 现有商品建立向量
make semantic-eval       # 真实中文检索小样本评测
```

重新启动 API 和 Celery worker/beat 以读取配置。平时运行 `make api`、`make web`、`make worker`、`make beat`。新建或修改商品后，Outbox 保留事件，由后台 worker 更新向量。模型失败时记录尝试次数并留待下次轮询重试，商品保存不会依赖模型服务。若只启动 API 而未运行 worker/beat，新商品仍支持普通搜索，但语义索引不会自动更新。

其他组织使用管理员 CLI：

```sh
uv run --project apps/api python apps/api/scripts/rebuild_embeddings.py --organization YOUR_ORG_CODE
```

此 CLI 使用明确的管理员权限解析组织，实际向量写入仍由 forge_app / RLS 执行；浏览器或模型参数不能决定 organization_id。不要把迁移数据库凭据给普通业务用户。

## 搜索行为与降级

先执行原有编码、条码、规格、关键词/trigram 和结构化过滤。仅在普通搜索结果为零时调用本地模型，补充最多 20 个语义候选。当前相似度下限为 0.55，可通过 `EMBEDDING_MIN_SIMILARITY` 调整；调低会增加误匹配，调整前需在真实商品样本上评测。

查询模型最多等待 2.5 秒，失败时继续返回普通搜索结果，不切换云端。冷启动或首次加载可能超出该等待时间，预先执行评测或索引可预热。停用商品、旧商品版本、不同模型指纹的向量均不参与匹配；改用其他模型必须重新建立索引。同一模型的权重指纹改变也会阻止调用，避免混合不同向量空间。语义文本格式另有版本号，格式改变会要求重建索引。SKU 和条码由精确搜索负责，不混入模型输入，避免英文编码片段干扰中文语义。

向量仅包含商品公开于本组织内的名称、简称、规格、型号与带名称的属性文本，不包含客户联系方式、价格或库存数据。每张向量表记录所属组织，并启用 FORCE RLS。初始使用组织范围内的精确余弦距离比较，尚未做大规模负载测试；数据量明显增大后再评估向量索引。

## 停止或关闭

将 `.env` 中 `EMBEDDING_ENABLED` 改为 `false` 并重启 API/worker，即关闭语义搜索。`docker compose --profile semantic stop ollama` 停止模型进程，保留已下载模型。只停止模型不影响原有商品搜索和资料管理。不得向公网开放 11438 端口；Docker 配置仅绑定 loopback，`OLLAMA_NO_CLOUD=1` 禁用云端模型功能。

## 验证边界

普通 CI 使用明确标注的向量测试夹具，验证维度、模型指纹、权限、RLS、版本并发、失效降级和 Outbox 重试；不把夹具当作真实模型效果。`make semantic-eval` 必须连接已下载的真实模型，独立验证中文语义匹配与响应时间。小样本通过不代表能替代五金尺寸/材质的确定性约束；有明确规格时仍优先精确匹配和属性过滤。

来源：[BGE-M3 模型](https://huggingface.co/BAAI/bge-m3)、[Ollama 模型分发](https://ollama.com/library/bge-m3)、[本地嵌入 API](https://docs.ollama.com/api/embed)、[禁用云端](https://docs.ollama.com/faq)。
