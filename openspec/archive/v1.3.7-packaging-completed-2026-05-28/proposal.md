# 打包发布提案：Vel'Koz V1.3.7（审视之眼 Pro 绿色版）

## 背景

用户需要"复制过去就能用"的发布包。当前版本要求用户预先安装 Python 3.11 及相关依赖，对非技术用户门槛过高。

## 方案

采用 **Python 3.11 embeddable** 绿色版策略：

1. 将 Python 3.11.9 embeddable 解压到发布包内的 `python\` 目录
2. 修改 `python*._pth` 文件，取消 `import site` 注释以启用 pip
3. 预装所有依赖到 `python\Lib\site-packages\`
4. 用户双击 `启动.bat` 即可运行，无需安装任何东西

## 发布包结构

```
Vel'Koz_V1.3.7/
├── 启动.bat                    ← 用户双击此文件
├── setup_embedded_python.bat   ← 开发者工具（初始化环境）
├── requirements.txt            ← 依赖清单
├── app.py                      ← Streamlit 主程序
├── css_styles.py               ← CSS/JS 常量
├── data_mixin.py               ← 数据处理模块
├── settings_mixin.py           ← 设置管理模块
├── export_mixin.py             ← 导出功能模块
├── zone_a.py                   ← 界面区域 A
├── zone_b.py                   ← 界面区域 B
├── zone_c.py                   ← 界面区域 C
├── disk_io.py                  ← 磁盘 IO 模块
├── utils.py                    ← 工具函数
├── export_utils.py             ← 导出工具
├── easter_eggs.py              ← 彩蛋功能
├── categories_config.json      ← 分类配置
├── scan_rules.json             ← 扫描规则
├── config/
│   └── settings.json           ← 用户设置
├── python/                     ← 内嵌 Python 3.11.9（绿色版）
│   ├── python.exe
│   ├── python311._pth
│   ├── Lib/site-packages/      ← 预装依赖
│   └── ...
└── 用户操作手册_v1.3.0.html    ← 操作手册
```

## 包含文件

| 类型 | 文件 |
|------|------|
| 核心代码 | app.py, *_mixin.py, zone_*.py, disk_io.py, utils.py, export_utils.py, css_styles.py, easter_eggs.py |
| 配置 | config/settings.json, categories_config.json, scan_rules.json |
| 启动 | 启动.bat, setup_embedded_python.bat, requirements.txt |
| 运行时 | python/（内嵌 Python + 预装依赖） |
| 文档 | 用户操作手册_v1.3.0.html |

## 排除文件

| 文件 | 原因 |
|------|------|
| __pycache__/ | 运行时自动生成 |
| openspec/ | 开发审查文档 |
| docs/ | 开发文档 |
| 项目更新维护日志.md | 开发日志 |
| app_error.log | 运行时自动生成 |
| app_v1.3.5_backup.py | 旧版本备份 |
| .claude/ | 开发工具配置 |
| 打包.bat | 开发者打包工具（不随包分发） |

## 实现步骤

1. ✅ 创建 `requirements.txt` — 依赖清单
2. ✅ 创建 `启动.bat` — 面向用户的启动脚本（首次运行自动初始化）
3. ✅ 创建 `setup_embedded_python.bat` — 开发者工具：下载嵌入式 Python + 安装依赖
4. ✅ 创建 `打包.bat` — 开发者工具：一键打包生成 ZIP
5. ✅ 更新 OpenSpec 文档
6. ✅ 更新维护日志

## 用户使用流程

1. 解压 `Vel'Koz_V1.3.7.zip`
2. 进入解压后的文件夹
3. 双击 `启动.bat`
4. 首次运行：自动下载并初始化 Python 环境（需联网，约 2-5 分钟）
5. 后续运行：直接启动，无需等待
6. 浏览器自动打开 Streamlit 页面
