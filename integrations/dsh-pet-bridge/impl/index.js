// dsh-pet 桌宠桥接：实现层版本注册表。
//
// 壳（../../index.js）静态 import 本文件挂载"当前实现"，热重载时按
// package.json.version + 本文件 mtime 探测变化，动态 import `./index.js?t=<mtime>`
// 拿到新代码。因此发版时只需：新增 impl/<new-version>/ 目录、把下面的
// re-export 指针更新到新版本、同步 package.json 的 version。
//
// 注意：本文件必须保持无状态（只做 re-export），热重载的 import 缓存破坏
// 依赖"本文件 mtime 变化"，发版时必然重写它（指针更新），天然满足。
export * from "./0.3.0/index.js";