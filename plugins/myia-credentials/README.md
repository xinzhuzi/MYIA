# myia-credentials — 凭证猎手数据源(aipocket)

官方场景件:凭证泄漏情报源,数据由自托管的 **aipocket** REST API 提供。
aipocket 为私有服务:**本插件不包含也不分发其实现,无 local compose**;
接入只走 endpoint + token 占位。本包只含声明与文档。

## 能力

- `provides: [aipocket]`:凭证猎手品类(`plugins/credentials.yaml`)经
  L1 `direct_api` 调 `GET /api/v1/leaks?page={page}` 拉取泄漏记录。
- remote 不可达/token 缺失 → 结构化 warning,品类照常跑(铁律)。

## local 模式(不适用)

aipocket 为私有部署服务,不随插件分发 compose。若未来提供公开的本地
部署形态,再按市场规范补 `docker-compose.yml` 并在 manifest 声明
`modes.local`。

## remote 模式(endpoint + token)

1. 把 token 写入钥匙链(规范名空间 `myia/<scope>/<name>`):

   ```bash
   myia secret set myia/credentials/token   # 粘贴你的 Bearer token
   ```

2. 品类 YAML 顶层声明(示例即 `plugins/credentials.yaml`):

   ```yaml
   plugin:
     id: myia-credentials
     requires: []
     modes:
       remote:
         endpoint: https://aipocket.example.com   # 换成你自己的部署地址
         token: keychain:myia/credentials/token
   ```

3. `myia doctor --json` 会把该 token 引用并入凭据体检;run 自检对
   endpoint 只在显式探测(`--probe`)时发请求,默认零网络。

## 凭据红线

- 仓库即公开:**绝不写死任何内网地址或真实端点** —— 文档与示例一律用
  `example.com` 占位;token 只走 `keychain:myia/<scope>/<name>` 引用,
  明文/env 展开值零入库。

## 安装 / 移除

```bash
myia plugin install plugins/myia-credentials
myia plugin list --json
myia plugin remove myia-credentials
```
