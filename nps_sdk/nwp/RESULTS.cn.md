[English](RESULTS.md) | 中文

# 实验性公开 result HTTP 客户端

`ResultHttpClient` 在宿主指定的HTTPS路由显式选择 data-results revision1，提供 create/read/aggregate/release、
不可变描述符围栏、肯定式发现和有界响应。这是开发源码新增接口，不代表已发布alpha.19包含该能力。
D20-05真实.NET/Python集成为IN_PROGRESS；六SDK资格仍属D20-08。

## 入口及所有权

操作者在 `httpx.AsyncClient` 配置实际TLS/mTLS信任，再传入固定网关路由与预期result issuer。身份、当前NIP/
资源/字段策略及模型出口由宿主负责；引用、模型输入subject/issuer不授予权限。公开腿仅请求`nps.data_results=1`，
独立下游投影由网关生成。不注册原生result路由、订阅或默认feature offer。

```python
import ssl
import uuid
import httpx
from nps_sdk.nwp import ResultHttpClient

trust = ssl.create_default_context(cafile="demo-ca.pem")
trust.load_cert_chain("owner.pem", "owner-key.pem")
async with httpx.AsyncClient(verify=trust, trust_env=False) as http:
    async with ResultHttpClient("https://node.example/gateway",
                                "urn:nps:node:example:results", http_client=http) as results:
        logical_key = uuid.uuid4()
        captured = await results.create("orders", logical_key, ttl_seconds=300)
        try:
            aggregate = await results.aggregate(captured, ["reason"], sum_field="amount_minor")
            page = await results.read(captured, 5, fields=["id", "reason", "amount_minor"])
        finally:
            await results.release(captured)
```

关闭客户端只关闭自己拥有的transport，传入的transport仍由调用方拥有；关闭连接不释放服务端结果。
服务端进程内保留、到期及当前来源权限仍适用，重启/端点替换不代表复制或重建旧结果。

## 请求与响应边界

每个POST前要求固定路由的肯定式、带版本NWM，校验精确issuer/route/auth/capability及result revision1。
拒绝版本回退和同版本内容变化；不重定向、语义改写、降级、自动重试或翻页。create要求UUID-v4，只接收
来源/filter/fields/order/TTL；来源白名单及query校验由服务端负责。请求不超过64KiB，使用整数JSON子集；
不支持的本地值在网络派发前失败。

read绑定issuer/result/snapshot/schema，limit为1..1000，可明确指定fields/cursor；COUNT/可选整数SUM最多4个
group字段。返回前校验描述符相等、原始类型、capsule/feature echo及解压后body/page/group上限。重复JSON键、
不安全整数/浮点/非法surrogate、错误权限发现数据均拒绝。单页有界，累计检查字节/行数/turn预算仍由应用执行。
create没有默认preview行。

`ResultHttpError` 只暴露有限status/code与`outcome_unknown`，不携带原始服务端body/凭据。发现失败零派发；
POST后传输/非法成功响应可能已经分配结果，只有明确使用原逻辑key/参数并重新获得当前授权才能恢复。
取消传播不代表回滚；不偷偷换key/issuer/endpoint。释放显式且由owner授权。

## 验证边界

MockTransport测试覆盖wire契约、发现零POST、错误绑定、上限、错误、取消与资源所有权，不证明真实传输身份
或部署资格；真实证据由[D20-05](../../../../docs/plans/2026-11-02-data-workflow-demo-implementation.md)提供。
依赖[.NET参考AC](../../../../docs/plans/2026-11-02-result-profile-ac-evidence.cn.md)及
[结果契约](../../../../spec/profiles/data-results.cn.md)。这些测试不调用付费模型。
