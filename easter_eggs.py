# easter_eggs.py — 维克兹彩蛋台词系统
import streamlit as st
import time


# === 台词定义 ===
QUOTES = {
    "first_load": "我看到了……所有数据。",
    "first_fail": "知识，通过解构获取。",
    "consecutive_fail_3": "有序的混乱……数据需要更多审视。",
    "consecutive_pass_5": "你的标注模式……可以预测。质量稳定。",
    "batch_complete": "分析完毕。批次已全部审视。",
    "sampling_start": "审视之眼……有目的地选取。",
    "paste_screenshot": "检测到异常。已记录。",
    "export_report": "知识，已封存。报告已导出。",
    "fatigue_50": "集中你的注意力。疲劳会影响判断。",
    "undo_status": "重新校准中……",
}

# 冷却时间（秒）
COOLDOWN = 30


def _trigger_toast(trigger_id: str, cooldown: int = COOLDOWN, once: bool = False):
    """检查冷却条件并触发 toast。返回 True 表示已触发。"""
    now = time.time()
    once_key = f"_ee_once_{trigger_id}"
    cd_key = f"_ee_cd_{trigger_id}"

    if once and st.session_state.get(once_key, False):
        return False

    last = st.session_state.get(cd_key, 0)
    if now - last < cooldown:
        return False

    quote = QUOTES.get(trigger_id, "")
    if not quote:
        return False

    st.toast(f"👁️ 审视之眼\n「{quote}」")
    st.session_state[cd_key] = now
    if once:
        st.session_state[once_key] = True
    return True


def init_consecutive_counters():
    """初始化连续判定计数器（仅在不存在时设置）"""
    if "_ee_consecutive_pass" not in st.session_state:
        st.session_state._ee_consecutive_pass = 0
    if "_ee_consecutive_fail" not in st.session_state:
        st.session_state._ee_consecutive_fail = 0
    if "_ee_total_judged" not in st.session_state:
        st.session_state._ee_total_judged = 0


def on_status_judged(status: str):
    """判定结果后调用，更新连续计数并触发相应台词"""
    init_consecutive_counters()

    st.session_state._ee_total_judged += 1

    if status == "不合格":
        st.session_state._ee_consecutive_fail += 1
        st.session_state._ee_consecutive_pass = 0

        if st.session_state._ee_consecutive_fail == 1:
            _trigger_toast("first_fail", once=True)
        if st.session_state._ee_consecutive_fail >= 3:
            _trigger_toast("consecutive_fail_3")

    elif status in ("合格", "修改后合格"):
        st.session_state._ee_consecutive_pass += 1
        st.session_state._ee_consecutive_fail = 0

        if st.session_state._ee_consecutive_pass >= 5:
            _trigger_toast("consecutive_pass_5")

    else:
        # 待定等其他状态，重置连续计数
        st.session_state._ee_consecutive_pass = 0
        st.session_state._ee_consecutive_fail = 0

    # 疲劳提醒
    if st.session_state._ee_total_judged == 50:
        _trigger_toast("fatigue_50", once=True)


def on_data_loaded():
    """首次加载数据成功时调用"""
    _trigger_toast("first_load", once=True)


def on_batch_complete():
    """全检完成时调用"""
    _trigger_toast("batch_complete")


def on_sampling_start():
    """抽检模式启动时调用"""
    _trigger_toast("sampling_start", once=True)


def on_screenshot_pasted():
    """粘贴错误截图时调用"""
    _trigger_toast("paste_screenshot")


def on_export_complete():
    """导出报告完成时调用"""
    _trigger_toast("export_report")


def on_undo_status():
    """撤销/修改已判定状态时调用"""
    _trigger_toast("undo_status")
