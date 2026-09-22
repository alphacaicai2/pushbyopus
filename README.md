# pushbyopus：历史 Relay 实现

## 维护与部署已迁移

Smart 与 Opus Relay 的统一系统现由 [Opussummary](https://github.com/alphacaicai2/Opussummary) 维护：

- Relay 后端位于新仓库的 [`relay/`](https://github.com/alphacaicai2/Opussummary/tree/main/relay)；智能简报、管理页面、飞书登录与代理位于同一仓库。
- 一份 Docker Compose 启动两个服务；只开放 Smart 入口，Relay 通过内部网络访问。
- 新部署、后续修改、问题修复请使用新仓库，具体步骤见[统一部署与迁移指南](https://github.com/alphacaicai2/Opussummary#从原来两个-compose-项目迁移)。
- 原生产 Relay 源码导入基准为 `ce148a4f09d5f417c36faec6eb215e9e473d3b0b` 的 `Opus/`，不代表本仓库其他原型目录全部迁移。

本仓库保留历史代码，没有删除旧实现。`Opus/`、根目录旧 Docker 配置和原型应用的运行说明仅供历史参考，不是当前统一系统的部署入口。请勿与统一部署同时运行另一套 Relay，否则可能重复推送。

迁移必须保留原 `Opus/config.json` 和完整 `Opus/data/`（包括去重记录、翻译缓存、轮询进度），停止旧服务后再复制；这些文件含运行数据或凭证，不应提交 Git。现有实例的具体迁移以新仓库 README 为准。

当前生产管理入口：[smart.vibexcap.com](https://smart.vibexcap.com/)。旧 Relay 域名仅跳转；旧 API 入口已停用。

历史详细说明：[Opus/README.md](Opus/README.md)。
