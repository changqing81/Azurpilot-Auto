from module.base.timer import Timer


class ProgressTracker:
    """语义进度指纹检测器。

    与设备层的截图指纹检测（Device._check_image_stuck）互补：
    - 截图指纹覆盖"画面完全静止"的卡死
    - 本类覆盖"画面在动（动画循环 / 点击有响应），但任务逻辑原地打转"的卡死

    工作方式：任务每完成一个有意义的步骤（一轮、一次移动、一次资源变化），
    调用 record() 上报一个描述当前逻辑位置的指纹。若指纹持续不变超过
    timeout 秒，is_stuck() 返回 True。

    注意：指纹必须来自任务语义（如行动力数值、海域编号、轮次状态），
    而不是截图像素——像素在动画场景下永远不同，检测不到目标场景。
    """

    def __init__(self, timeout=300):
        """
        Args:
            timeout (int | float): 指纹持续不变判卡死的秒数。
                使用 count=0 的 Timer，只按时间判定，适配低频检查
                （任务轮边界每轮才调用一次，无法满足访问计数下限）。
        """
        self.timeout = timeout
        self._last_fingerprint = None
        self._timer = Timer(timeout, count=0)

    def record(self, fingerprint) -> bool:
        """上报当前逻辑位置指纹。

        Args:
            fingerprint: 当前逻辑位置（任意类型，内部转字符串比较）。

        Returns:
            bool: 与上次相比是否有进展（指纹变化）。
        """
        fingerprint = str(fingerprint)
        if self._last_fingerprint is None or fingerprint != self._last_fingerprint:
            self._last_fingerprint = fingerprint
            self._timer.clear()
            return True
        # 指纹不变：从第二次起计时（start() 对已启动的计时器无操作）
        self._timer.start()
        return False

    def is_stuck(self) -> bool:
        """检查逻辑位置是否已持续不变超过 timeout。

        Returns:
            bool: 卡死返回 True。
        """
        if self._last_fingerprint is None:
            return False
        self._timer.start()
        return self._timer.reached()

    @property
    def stuck_duration(self) -> float:
        """当前指纹已持续不变的秒数（自最近一次指纹变化起）。"""
        return self._timer.current_time()

    def clear(self):
        """清除状态。

        用于无法取得可靠指纹时按"有进展"处理，避免误判。
        """
        self._last_fingerprint = None
        self._timer.clear()
