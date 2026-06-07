# 任务清单：Vel'Koz V1.3.7 打包发布

## 1. 创建依赖清单

- [x] 1.1 创建 `requirements.txt`（streamlit, pandas, Pillow, XlsxWriter, streamlit-paste-button）

## 2. 创建启动脚本

- [x] 2.1 创建 `启动.bat` — 面向用户，首次运行自动调用 setup_embedded_python.bat
- [x] 2.2 端口自动检测（8502 起，自动递增避开占用）

## 3. 创建开发者工具

- [x] 3.1 创建 `setup_embedded_python.bat` — 下载 Python 3.11.9 embeddable + 安装 pip + 安装依赖
- [x] 3.2 创建 `打包.bat` — 一键复制文件 + 生成 Vel'Koz_V1.3.7.zip

## 4. 更新 OpenSpec 文档

- [x] 4.1 更新 `.openspec.yaml` — 状态改为 completed
- [x] 4.2 重写 `proposal.md` — 包含嵌入式 Python 策略、发布包结构、文件清单
- [x] 4.3 重写 `tasks.md` — 更新任务清单

## 5. 更新维护日志

- [x] 5.1 追加打包发布记录到 `项目更新维护日志.md`

## 验证结果

| 任务 | 状态 | 备注 |
|------|------|------|
| 1.1 创建 requirements.txt | ✅ 完成 | 5 个依赖 |
| 2.1 创建 启动.bat | ✅ 完成 | 自动初始化 + 端口检测 |
| 3.1 创建 setup_embedded_python.bat | ✅ 完成 | 5 步自动初始化 |
| 3.2 创建 打包.bat | ✅ 完成 | 4 步一键打包 |
| 4.x 更新 OpenSpec | ✅ 完成 | 全部 3 个文件 |
| 5.1 更新维护日志 | ✅ 完成 | - |

## 待用户验证

- [ ] 运行 `setup_embedded_python.bat` 初始化内嵌 Python 环境
- [ ] 运行 `打包.bat` 生成 ZIP
- [ ] 解压 ZIP 到新目录，双击 `启动.bat` 验证
