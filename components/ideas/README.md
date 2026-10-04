# 想法收集器 · 安卓版（PWA）

手机端「想法收集器」，功能与 Windows 桌面版一致：列表 / 搜索 / 新建 / 编辑 / 删除 / 图片附件。
数据直接读写 Syncthing 同步文件夹里的 `data/ideas.json` + `data/images/`，与桌面版完全兼容，无需第三方中间服务。

## 原理
- 使用浏览器 File System Access API（`showDirectoryPicker`）选择已同步到手机的「想法收集器」文件夹
- 文件夹句柄保存在 IndexedDB，下次打开自动恢复；读写前自动请求授权
- 增删改直接写回 `ideas.json`，Syncthing 负责同步到电脑，桌面版打开即是最新数据
- PWA 可添加到手机主屏幕，离线可用

## 使用
1. 手机浏览器（Chrome / Edge 132+）打开部署地址
2. 点「选择同步文件夹」，选中 Syncthing 同步的「想法收集器」文件夹
3. 授权「读写」后即可查看/编辑想法
4. 浏览器菜单 →「添加到主屏幕」即可像 App 一样使用

## 本地开发
```bash
python -m http.server 8080
```
> 注意：File System Access API 仅支持 HTTPS 或 localhost。

## 文件
- `index.html` / `app.css` / `app.js` — 应用本体
- `manifest.webmanifest` / `sw.js` / `icon-*.png` — PWA 支持
- `test_logic.cjs` / `test_data.cjs` — 逻辑与数据层测试