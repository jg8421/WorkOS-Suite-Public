# 手机独立上传 OneDrive

0.3.0 使用微软 Microsoft Graph 的 HTTPS 接口上传；不读取其他 App 的登录令牌。手机里 OneDrive 已登录，不代表 Personal Memory 已被允许写文件。需要给 Personal Memory 注册自己的公共客户端并完成一次授权。

## 首次设置

1. 在浏览器打开 https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade ，用保存目标 OneDrive 的公司账户登录。
2. 点击“新注册”，名称填写 `Personal Memory Assistant`，账户类型选当前组织。这个设备代码登录方案不要求客户端密钥，也不需要填写 Web 回调地址。
3. 注册完成后，复制“应用程序（客户端）ID”。在“身份验证 / 高级设置”开启“允许公共客户端流”。
4. 在“API 权限”添加 Microsoft Graph 的**委托权限** `Files.ReadWrite`。不要添加应用程序权限，不要添加 `Files.ReadWrite.All`。如果组织限制用户注册应用或用户同意，需要组织管理员为这个应用批准。
5. 在手机 Personal Memory 中填写 Client ID 和组织域名，点击“连接 Microsoft / OneDrive”。应用显示一次性代码，点击“复制授权码并打开微软”。在微软官方页面输入代码、选择同一公司账户，查看权限并确认。
6. 授权成功后会自动开启“手机直接上传 OneDrive”。查看“最近写入云端”的时间；必须看到实际上传成功才能视为已启用直传。

官方上传接口要求独立的应用身份和委托授权。`Files.ReadWrite` 是该接口支持的最低委托权限，权限本身覆盖登录用户的文件；本程序固定只在下述已存在的目标路径下写入记忆批次。持续登录所需刷新凭据以 Android Keystore AES-GCM 加密保存在手机本机，不存入 OneDrive，也不提交到 GitHub。

## 同步方式

```text
手机 → Microsoft Graph HTTPS → OneDrive/data/inbox/android/独立批次.jsonl
                                               ↓ OneDrive 桌面同步
                                        Windows 自动合并 → 记忆库 / MCP
电脑当前应用、可读取页面上下文、Codex/DSH 对话 → 本地记忆库 → OneDrive
```

云端根路径由 App 的“OneDrive 目标路径”配置，默认 `Personal Memory/data`。该根目录必须已存在；程序仅在其下创建 `inbox/android`。不同设备应配置同一个数据根目录，避免选错账户或目标路径。

每次最多 20 条记录，文件名由批次 SHA-256 决定。手机只写独立文件，电脑负责合并，不同时改写总账。重复传输及通过旧电脑接口重复收到的同一 `sourceId` 不会生成重复活跃记录。手机看到微软确认文件名称、ID 和字节数后才移除对应队列前缀。离线记录保留在手机，网络恢复后重试。

自动上传最多约每 30 秒合并一次新批次，减少频繁联网；手动同步可立即尝试。Android 省电机制仍可能延迟执行。电脑关机不影响已经授权的手机上传；但总账、可读记忆视图和当前本地 MCP 要等电脑重新上线后合并更新。

停止手机新增采集使用 App 的暂停按钮；待上传队列仍可能继续同步。“断开 OneDrive 授权”移除本机保存的刷新凭据并回到旧电脑同步方式，保留待发送记录。若要从微软账户撤销该应用的许可，应在微软账户/组织应用授权页面操作。

## 当前验证状态（2026-10-01）

- 0.3.1 修复连接按钮反馈：按钮附近持续显示请求状态；缺失/无效 Client ID 立即弹窗；网络或微软返回错误会弹窗，不再仅显示在页面下方。已构建、签名并安装；真实设备上的点击反馈仍需确认，不代表已完成 Microsoft 授权。
- 0.3.0 APK 构建、签名、安装已完成；升级后无障碍采集仍绑定。
- Windows 前台上下文已产生实际入库记录。
- 云端批次校验、导入、跨 LAN 去重、重启去重、拒绝损坏/未知来源记录及脱敏测试通过。
- 使用 `node scripts/verify-cloud-inbox.mjs` 的明确标注合成批次，已验证运行中的 Windows 服务自动合并本地 inbox；该测试会保留测试文件和测试记录，不代表手机直传或 OneDrive 云端同步成功。
- Windows OneDrive 客户端正在运行，但新增 inbox 在手机 OneDrive 目录中尚未确认出现；本地文件写入不能代替云端上传验证。
- 手机真实 Microsoft 授权和云端 PUT 尚待完成；没有把“代码已安装”视为“直传已经成功”。

参考：[Microsoft Graph 上传](https://learn.microsoft.com/en-us/graph/api/driveitem-put-content?view=graph-rest-1.0)、[Microsoft 设备代码授权](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)。
