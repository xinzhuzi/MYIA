# plugins/community/ — 社区插件发行目录

本目录收纳**社区场景件的发行文件**(manifest + 文档 + compose 的插件包),
核心仓库只托管这些声明性文件,**不托管插件实现** —— 重依赖与代码全部留在
作者自己的仓库(与官方六件 `plugins/myia-*/` 同一套市场规范,manifest 校验
见 `src/myia/plugins/manifest.py`)。

## 铁律(与全市场一致)

**任何 plugin 装不上、配置坏、remote 不可达,核心流水线照常跑通。**
插件问题只降级为结构化 warning finding(`myia doctor --json` /
`myia plugin list --json` 可见),绝不拦 run。

## 收录规范

1. **一个插件一个目录**,目录名 == `plugin.yaml` 的 `id`;根下必备:
   - `plugin.yaml`(manifest,过 `myia plugin install` 的 fail-fast 校验);
   - `README.md`(local(docker compose)/ remote(endpoint+token)双路径
     说明;无 local 模式的插件须写明「不适用」及原因);
   - `docker-compose.yml`(有 local 模式时;零明文凭据)。
2. **命名**:社区插件不得占用官方保留前缀 `myia-`;惯例
   `<作者/组织>-<用途>`(如 `acme-price-tracker`)。id 规则:小写字母/数字/
   连字符/下划线,字母数字开头,2-64 字符。
3. **凭据红线**(仓库即公开):
   - token/cookie/口令零明文,manifest 里只允许
     `keychain:myia/<scope>/<name>` 引用;
   - endpoint/地址一律用占位域(`example.com`);内网地址零容忍
     (127.0.0.1 作为 compose 端口绑定除外);
   - 生产语料、私有系统痕迹零入库。
4. **许可**:上游项目按其 license 以「声明依赖 + 文档引用 + compose 引用
   官方镜像/上游源码」接入;GPL/AGPL 类上游**不复制其源码进本仓库**。
5. **版本矩阵**:`compatible` 声明兼容的 myia 版本范围(如 `">=0.1,<2.0"`);
   不兼容在 install 期结构化拒绝,已装的降级为 warning。

## 索引

| 插件 id | 一句话 | 许可 | 上游 |
| ------- | ------ | ---- | ---- |
| (暂无收录) | | | |

收录流程:提交 PR,把插件目录放进本目录并在上表加一行;维护者按上述
规范核验 manifest 与红线后合并。
