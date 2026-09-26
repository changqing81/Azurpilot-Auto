"""WebUI 的安全判断、模板读取和轻量 HTML 构造函数。"""

from html import escape as html_escape

from module.webui.app_dependencies import (
    Path,
    State,
    logger,
    os,
    secrets,
    string,
    t,
)

WEBUI_AUTO_PASSWORD_FILE = "password.txt"
DEMO_DEVICE_ID_TEXT = "此程序是为了演示用途构建的版本/This application is a version built for demonstration purposes."


def is_demo_mode():
    """
    判断是否处于演示环境。

    Returns:
        bool: True 表示 DEMO=1。
    """
    return os.environ.get("DEMO") == "1"


def is_public_webui_host(host):
    """
    判断 WebUI 是否监听所有网络接口。

    Args:
        host (str): WebUI 监听地址。

    Returns:
        bool: True 表示 WebUI 允许所有设备访问。
    """
    host = str(host or "").strip().lower()
    return host in ("0.0.0.0", "::", "[::]")


def is_webui_password_set(password):
    """
    判断 WebUI 密码是否有效设置。

    Args:
        password: WebUI 密码配置。

    Returns:
        bool: True 表示密码包含非空白字符。
    """
    return bool(str(password or "").strip())


def generate_webui_password(length=32):
    """
    生成包含大小写字母和数字的 WebUI 密码。

    Args:
        length (int): 密码长度。

    Returns:
        str: 随机密码。
    """
    letters_upper = string.ascii_uppercase
    letters_lower = string.ascii_lowercase
    digits = string.digits
    alphabet = letters_upper + letters_lower + digits
    password = [
        secrets.choice(letters_upper),
        secrets.choice(letters_lower),
        secrets.choice(digits),
    ]
    password.extend(secrets.choice(alphabet) for _ in range(length - len(password)))
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


def ensure_public_webui_password(key):
    """
    公网监听且未设置密码时自动生成密码。

    Args:
        key: 命令行或部署配置中的 WebUI 密码。

    Returns:
        tuple[str | None, str | None]: 有效密码和失败原因。
    """
    if is_demo_mode():
        return key, None

    host = State.webui_host or State.deploy_config.WebuiHost
    if not is_public_webui_host(host) or is_webui_password_set(key):
        return key, None

    try:
        password = generate_webui_password()
        from deploy.atomic import atomic_write

        atomic_write(WEBUI_AUTO_PASSWORD_FILE, f"{password}\n")
        State.deploy_config.Password = password
        logger.warning(
            f"[WebUI] WebUI 已自动生成密码，请在根目录 {WEBUI_AUTO_PASSWORD_FILE} 查看。"
        )
        return password, None
    except Exception as e:
        logger.exception(f"WebUI 自动生成密码失败: {e}")
        return None, str(e)


def timedelta_to_text(delta=None):
    """将时间差数据转换为仪表盘本地化文本。

    Args:
        delta: 时间差字典或空值。

    Returns:
        str: 本地化相对时间。
    """
    time_delta_name_suffix_dict = {
        "Y": "YearsAgo",
        "M": "MonthsAgo",
        "D": "DaysAgo",
        "h": "HoursAgo",
        "m": "MinutesAgo",
        "s": "SecondsAgo",
    }
    time_delta_name_prefix = "Gui.Dashboard."
    time_delta_name_suffix = "NoData"
    time_delta_display = ""
    if isinstance(delta, dict):
        for _key in delta:
            if delta[_key]:
                time_delta_name_suffix = time_delta_name_suffix_dict[_key]
                time_delta_display = delta[_key]
                break
    time_delta_display = str(time_delta_display)
    time_delta_name = time_delta_name_prefix + time_delta_name_suffix
    return time_delta_display + t(time_delta_name)


def read_webapp_template(filename: str) -> str:
    """读取 WebUI 复用的 HTML 模板。

    Args:
        filename: 模板文件名。

    Returns:
        str: 模板内容。
    """
    template_path = Path(os.getcwd()) / "webapp" / filename
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def build_title_block(
    title: str, margin_top: int = 12, margin_bottom: int = 8, font_weight: int = 600
) -> str:
    """构造统一标题块。

    Args:
        title: 标题文本。
        margin_top: 顶部间距。
        margin_bottom: 底部间距。
        font_weight: 标题字重。

    Returns:
        str: 标题块 HTML。
    """
    tpl = read_webapp_template("title_block.html")
    return tpl.format(
        title=title,
        margin_top=margin_top,
        margin_bottom=margin_bottom,
        font_weight=font_weight,
    )


def build_muted_notice(text: str) -> str:
    """构造弱强调提示块。

    Args:
        text: 提示文本。

    Returns:
        str: 提示块 HTML。
    """
    tpl = read_webapp_template("muted_notice.html")
    return tpl.format(text=text)


def build_fold_block(
    title: str, body_html: str, digest: str = "", open_by_default: bool = False
) -> str:
    """构造可折叠区块（对应 statistics-v2 原型的 .fold / .fold-digest）。

    用于把体积大但非首要的表格收进 <details>，摘要行右侧可放一句摘要。

    Args:
        title: 摘要标题（纯文本，内部会转义）。
        body_html: 折叠体内嵌 HTML。
        digest: 摘要行右侧的补充说明（纯文本）。
        open_by_default: 是否默认展开。

    Returns:
        str: 折叠块 HTML。
    """
    tpl = read_webapp_template("fold_block.html")
    return tpl.format(
        title=html_escape(str(title)),
        digest=html_escape(str(digest)),
        body_html=body_html,
        open_attr=" open" if open_by_default else "",
    )


def build_metric_grid(labels, values, extra_style: str = "") -> str:
    """构造指标组卡片（对应 statistics-v2 原型的 .metric-grid / .metric）。

    把原先「表头一行 + 数值一行」的单行表格换成一组小卡片，
    数值口径与列顺序完全不变，只改呈现。

    Args:
        labels: 指标名列表（与 values 一一对应）。
        values: 指标值列表，可含 HTML（调用方自行保证已转义）。
        extra_style: 附加 CSS 样式。

    Returns:
        str: 指标组 HTML。
    """
    cells = "".join(
        [
            '<div class="metric">'
            f'<div class="metric-label" title="{html_escape(str(label))}">'
            f"{html_escape(str(label))}</div>"
            f'<div class="metric-value">{value}</div>'
            "</div>"
            for label, value in zip(labels, values)
        ]
    )
    tpl = read_webapp_template("metric_grid.html")
    return tpl.format(cells=cells, extra_style=extra_style)


def build_chip_row(chips) -> str:
    """把若干 (标签, 值) 渲染成一排胶囊（对应 statistics-v2 的 .chip-row / .chip）。

    用于折叠块体内的指标摘要行 —— 比裸文字更易读，透明主题下也不会糊在壁纸上。

    Args:
        chips: (标签, 值) 二元组序列；标签与值都会转义，值加 <b> 强调。

    Returns:
        str: 胶囊行 HTML。
    """
    cells = "".join(
        f'<span class="st-chip">{html_escape(str(label))}'
        f"<b>{html_escape(str(value))}</b></span>"
        for label, value in chips
    )
    return f'<div class="st-chip-row">{cells}</div>'


def build_simple_table(headers, rows, extra_style: str = "", numeric_from=None) -> str:
    """构造统计用的简洁表格。

    Args:
        headers: 表头列表。
        rows: 表格行数据。
        extra_style: 附加 CSS 样式。
        numeric_from: 从该列序号（含）起的列按右对齐渲染，表头一并对齐；
            不传时保持原行为（表头左对齐、单元格居中）。

    Returns:
        str: 表格 HTML。
    """
    tpl = read_webapp_template("simple_table.html")
    headers = list(headers)

    def is_numeric(index: int) -> bool:
        return numeric_from is not None and index >= numeric_from

    thead_cells = "".join(
        [
            f'<th style="text-align:{"right" if is_numeric(i) else "left"};'
            f'padding:6px">{h}</th>'
            for i, h in enumerate(headers)
        ]
    )
    # data-th 供窄屏「表格转卡片列表」时以 ::before 带出字段名
    # （statistics-alas.css 的 @media (max-width:720px) 分支）。
    tbody_rows = "".join(
        [
            "<tr>"
            + "".join(
                [
                    '<td data-th="{}" style="text-align:{};padding:6px">{}</td>'.format(
                        html_escape(str(headers[index])) if index < len(headers) else "",
                        "right" if is_numeric(index) else "center",
                        value,
                    )
                    for index, value in enumerate(row)
                ]
            )
            + "</tr>"
            for row in rows
        ]
    )
    return tpl.format(
        thead_cells=thead_cells,
        tbody_rows=tbody_rows,
        extra_style=extra_style,
    )


def build_recommendation_box(text: str) -> str:
    """构造推荐提示框。

    Args:
        text: 提示文本。

    Returns:
        str: 提示框 HTML。
    """
    tpl = read_webapp_template("recommendation_box.html")
    return tpl.format(text=text)
