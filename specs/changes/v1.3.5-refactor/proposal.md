# 重构提案：app.py 单体拆分

## 背景

`app.py` 是一个 3,034 行的单文件单类 Streamlit 应用，`AcceptanceApp` 类包含 61 个方法，38+ 个 session_state 变量。随着 v1.1.7→v1.3.5 的 7 次迭代，维护成本指数增长。

## 架构方案：Mixin 多继承

```python
class AcceptanceApp(DataMixin, SettingsMixin, ExportMixin,
                    ZoneAMixin, ZoneBMixin, ZoneCMixin):
    def __init__(self): ...
    def run(self): ...
```

## 目标文件结构

```
1.3.5/
  app.py              (~200行)  入口 + __init__ + run()
  css_styles.py       (~150行)  CSS/JS 常量
  disk_io.py          (~230行)  模块级缓存函数
  data_mixin.py       (~430行)  数据/CSV/导航
  settings_mixin.py   (~490行)  设置/配置/标签
  export_mixin.py     (~470行)  导出/弹窗
  zone_a.py           (~250行)  A区
  zone_b.py           (~450行)  B区
  zone_c.py           (~460行)  C区
  utils.py            (不变)
  export_utils.py     (不变)
```

## 分阶段执行计划

| Phase | 内容 | 风险 |
|-------|------|------|
| 1 | 提取 CSS 常量到 css_styles.py | 零风险 |
| 2 | 提取模块级函数到 disk_io.py | 低风险 |
| 3 | 提取 DataMixin (22 个方法) | 中等风险 |
| 4 | 提取 SettingsMixin (15 个方法) | 中等风险 |
| 5 | 提取 ExportMixin (10 个方法) | 中等风险 |
| 6 | 提取 Zone Mixins | 中等风险 |
| 7 | 清理废弃代码 + 最终验证 | 低风险 |

## 风险缓解措施

1. **版本副本**：重构前复制当前 app.py 为 app_v1.3.5_backup.py
2. **逐 Phase 验证**：每完成一个 Phase 都做全流程测试
3. **导入顺序**：Mixin 的 MRO 顺序必须正确
4. **session_state 不变**：不改变任何 session_state 键名
5. **@st.dialog 装饰器**：必须保留在类方法上
