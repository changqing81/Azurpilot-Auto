(function () {
    // 任务资源时间轴：任务节点沿水平主线按时间排列，
    // 节点上方标注增加（涨了）、下方标注消耗（消耗了），与手绘稿一致。
    // 画布不做底色（clearRect 透出面板背景），配色全部读 --rd-* CSS 变量，
    // 随主题（含运行期切换）自动换肤。
    var tasks = __TASKS__;
    var chartId = "__CHART_ID__";
    var TXT_GAIN = "__TXT_GAIN__";
    var TXT_LOSS = "__TXT_LOSS__";
    var TXT_TIMES = "__TXT_TIMES__";  // 含 {n} 占位符
    var TXT_MORE = "__TXT_MORE__";    // 含 {n} 占位符

    var TOP_N = 3;               // 节点上下最多直接标注的条目数
    var MIN_COL = 104;           // 单个节点列最小宽度（像素）
    var ENTRY_GAP = 18;          // 条目行距
    var MORE_GAP = 16;           // "+N 项" 折叠行高

    var cv = document.getElementById(chartId);
    if (!cv) return;
    var tipEl = document.getElementById(chartId + "_tip");

    var dpr = window.devicePixelRatio || 1;
    var n = tasks.length;
    if (n < 1) return;

    var W, H, pad, gW, colW, cy;
    var firstVisible = 0;        // 最左侧可见节点索引
    var visibleCount = 0;        // 可见节点数
    var defaultVisible = 0;
    var hoverIdx = null;
    var PAL = {};                // 主题调色板（CSS 变量缓存）

    var cleanupHandlers = [];
    var initialRenderTimer = null;

    window.__resourceDeltaChartCleanups = window.__resourceDeltaChartCleanups || {};
    if (window.__resourceDeltaChartCleanups[chartId]) {
        window.__resourceDeltaChartCleanups[chartId]();
    }
    var cleanup = function () {
        cleanupHandlers.forEach(function (item) {
            item.target.removeEventListener(item.type, item.handler, item.options);
        });
        cleanupHandlers = [];
        if (initialRenderTimer !== null) clearTimeout(initialRenderTimer);
        initialRenderTimer = null;
        if (window.__resourceDeltaChartCleanups[chartId] === cleanup) {
            delete window.__resourceDeltaChartCleanups[chartId];
        }
    };
    window.__resourceDeltaChartCleanups[chartId] = cleanup;

    function addListener(target, type, handler, options) {
        if (!target) return;
        target.addEventListener(type, handler, options);
        cleanupHandlers.push({ target: target, type: type, handler: handler, options: options });
    }

    // ---- 主题调色板 ----

    function cssVar(name, fallback) {
        var v = getComputedStyle(document.documentElement).getPropertyValue(name);
        v = (v || "").trim();
        return v || fallback;
    }

    function readPalette() {
        PAL = {
            line: cssVar("--rd-line", "#3a3a58"),
            nodeBg: cssVar("--rd-node-bg", "#24243c"),
            nodeBorder: cssVar("--rd-node-border", "#3d3d5c"),
            nodeBgHover: cssVar("--rd-node-bg-hover", "#2e2e50"),
            nodeBorderHover: cssVar("--rd-node-border-hover", "#64b5f6"),
            nodeText: cssVar("--rd-node-text", "#dfe6ee"),
            entryName: cssVar("--rd-entry-name", "#b8c2cc"),
            gain: cssVar("--rd-gain", "#26a69a"),
            loss: cssVar("--rd-loss", "#ef5350"),
            guide: cssVar("--rd-guide", "rgba(100,181,246,0.18)"),
            more: cssVar("--rd-more", "#5c6470"),
            muted: cssVar("--rd-muted", "#8a93a3")
        };
    }

    // 运行期主题切换：set_theme 会派发 alas-theme-change 事件，
    // 此时主题 CSS 已重注入，直接重读变量并重绘。
    addListener(window, "alas-theme-change", function () {
        readPalette();
        draw();
    });

    // ---- 布局 ----

    // 内容半区高度（增加在上 / 消耗在下），用于自适应画布高度与主线定位
    function contentHalf() {
        var up = 0, down = 0;
        for (var i = 0; i < n; i++) {
            var t = tasks[i];
            var u = t.gains.length
                ? Math.min(TOP_N, t.gains.length) * ENTRY_GAP
                  + (t.gains.length > TOP_N ? MORE_GAP : 0)
                : 0;
            var d = t.losses.length
                ? Math.min(TOP_N, t.losses.length) * ENTRY_GAP
                  + (t.losses.length > TOP_N ? MORE_GAP : 0)
                : 0;
            if (u > up) up = u;
            if (d > down) down = d;
        }
        return { up: up, down: down };
    }

    function computeLayout() {
        // 自适应高度：按全部任务的标注体量决定，平移/缩放时高度不跳变
        var half = contentHalf();
        var need = half.up + 26 + half.down + 64;
        var h = Math.max(240, Math.min(430, need));
        cv.style.height = h + "px";

        W = cv.clientWidth;
        H = cv.clientHeight;
        if (!W || !H) { W = cv.parentElement.clientWidth || 900; H = h; }
        cv.width = W * dpr; cv.height = H * dpr;
        cv.style.width = W + "px"; cv.style.height = H + "px";
        pad = { t: 14, r: 18, b: 14, l: 18 };
        gW = W - pad.l - pad.r;
        defaultVisible = Math.max(1, Math.min(n, Math.floor(gW / MIN_COL)));
        if (visibleCount <= 0 || visibleCount > n) visibleCount = defaultVisible;
        colW = gW / visibleCount;
        // 主线位置：内容块在面板内垂直居中
        var free = H - pad.t - pad.b - (half.up + 26 + half.down);
        cy = Math.round(pad.t + half.up + 13 + Math.max(0, free) / 2);
    }

    function clampPan() {
        var maxFirst = Math.max(0, n - visibleCount);
        firstVisible = Math.max(0, Math.min(maxFirst, firstVisible));
    }

    function xOfCol(col) {
        return pad.l + (col + 0.5) * colW;
    }

    function nodeAtX(mx) {
        if (mx < pad.l || mx > W - pad.r) return null;
        var col = Math.floor((mx - pad.l) / colW);
        var idx = firstVisible + Math.max(0, Math.min(col, visibleCount - 1));
        return idx < n ? idx : null;
    }

    function truncate(ctx, text, maxW) {
        if (ctx.measureText(text).width <= maxW) return text;
        while (text.length > 1 && ctx.measureText(text + "…").width > maxW) {
            text = text.slice(0, -1);
        }
        return text + "…";
    }

    function roundRect(ctx, x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + w - r, y);
        ctx.arcTo(x + w, y, x + w, y + r, r);
        ctx.lineTo(x + w, y + h - r);
        ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
        ctx.lineTo(x + r, y + h);
        ctx.arcTo(x, y + h, x, y + h - r, r);
        ctx.lineTo(x, y + r);
        ctx.arcTo(x, y, x + r, y, r);
        ctx.closePath();
    }

    // 单条标注：资源色圆点 + 资源名 + 红绿数额，整行在节点列内居中
    function drawEntry(ctx, entry, x, y, isGain, maxW) {
        var amount = (isGain ? "+" : "-") + fmtVal(entry.value);
        ctx.textBaseline = "middle";
        ctx.font = "bold 11px -apple-system, sans-serif";
        var amtW = ctx.measureText(amount).width;
        ctx.font = "11px -apple-system, sans-serif";
        var name = truncate(ctx, entry.name, Math.max(12, maxW - amtW - 18));
        var nameW = ctx.measureText(name).width;

        var dotR = 3;
        var dotSpace = dotR * 2 + 4;
        var contentW = dotSpace + nameW + 4 + amtW;
        var sx = x - contentW / 2;

        ctx.beginPath();
        ctx.arc(sx + dotR, y, dotR, 0, Math.PI * 2);
        ctx.fillStyle = entry.color;
        ctx.fill();

        var tx = sx + dotSpace;
        ctx.textAlign = "left";
        ctx.fillStyle = PAL.entryName;
        ctx.fillText(name, tx, y);
        tx += nameW + 4;
        ctx.font = "bold 11px -apple-system, sans-serif";
        ctx.fillStyle = isGain ? PAL.gain : PAL.loss;
        ctx.fillText(amount, tx, y);
    }

    function drawMoreLine(ctx, count, x, y) {
        ctx.fillStyle = PAL.more;
        ctx.font = "10px -apple-system, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(TXT_MORE.replace("{n}", count), x, y);
    }

    function draw() {
        var ctx = cv.getContext("2d");
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        // 不绘制底色：透出 .rd-panel 的主题背景（透明主题下透出壁纸）
        ctx.clearRect(0, 0, W, H);

        // 主时间轴线 + 右侧箭头
        ctx.strokeStyle = PAL.line;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(pad.l, cy);
        ctx.lineTo(W - pad.r, cy);
        ctx.stroke();
        ctx.fillStyle = PAL.line;
        ctx.beginPath();
        ctx.moveTo(W - pad.r + 6, cy);
        ctx.lineTo(W - pad.r - 4, cy - 5);
        ctx.lineTo(W - pad.r - 4, cy + 5);
        ctx.closePath();
        ctx.fill();

        var boxW = Math.min(92, Math.max(54, colW - 22));
        var boxH = 26;

        for (var col = 0; col < visibleCount; col++) {
            var idx = firstVisible + col;
            if (idx >= n) break;
            var task = tasks[idx];
            var x = xOfCol(col);
            var hovered = idx === hoverIdx;

            if (hovered) {
                // 悬浮：淡色纵向导引
                ctx.strokeStyle = PAL.guide;
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(x, pad.t);
                ctx.lineTo(x, H - pad.b);
                ctx.stroke();
            }

            // 节点盒（悬浮时高亮）
            var bx = x - boxW / 2, by = cy - boxH / 2;
            roundRect(ctx, bx, by, boxW, boxH, 8);
            ctx.fillStyle = hovered ? PAL.nodeBgHover : PAL.nodeBg;
            ctx.fill();
            ctx.strokeStyle = hovered ? PAL.nodeBorderHover : PAL.nodeBorder;
            ctx.lineWidth = 1.5;
            ctx.stroke();

            ctx.fillStyle = PAL.nodeText;
            ctx.font = "11px -apple-system, sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(truncate(ctx, task.name, boxW - 10), x, cy + 0.5);

            var entryMaxW = colW - 8;

            // 上方：增加（自下而上堆叠）
            var gainN = Math.min(TOP_N, task.gains.length);
            for (var gi = 0; gi < gainN; gi++) {
                drawEntry(ctx, task.gains[gi], x, by - 12 - gi * ENTRY_GAP, true, entryMaxW);
            }
            if (task.gains.length > TOP_N) {
                drawMoreLine(ctx, task.gains.length - TOP_N, x, by - 12 - TOP_N * ENTRY_GAP - 6);
            }

            // 下方：消耗（自上而下堆叠）
            var lossN = Math.min(TOP_N, task.losses.length);
            for (var li = 0; li < lossN; li++) {
                drawEntry(ctx, task.losses[li], x, by + boxH + 14 + li * ENTRY_GAP, false, entryMaxW);
            }
            if (task.losses.length > TOP_N) {
                drawMoreLine(ctx, task.losses.length - TOP_N, x, by + boxH + 14 + TOP_N * ENTRY_GAP + 6);
            }
        }
    }

    // ---- 悬浮详情 ----

    function tooltipRow(label, color, amountText, amountColor) {
        var row = document.createElement("div");
        row.style.display = "flex";
        row.style.alignItems = "center";
        row.style.gap = "6px";
        var dot = document.createElement("span");
        dot.style.width = "8px";
        dot.style.height = "8px";
        dot.style.borderRadius = "4px";
        dot.style.background = color;
        row.appendChild(dot);
        var nameSpan = document.createElement("span");
        nameSpan.textContent = label;
        row.appendChild(nameSpan);
        var val = document.createElement("b");
        val.textContent = amountText;
        val.style.color = amountColor;
        val.style.marginLeft = "auto";
        row.appendChild(val);
        return row;
    }

    function sectionHeader(text, color) {
        var div = document.createElement("div");
        div.textContent = text;
        div.style.color = color;
        div.style.fontWeight = "600";
        div.style.margin = "6px 0 2px";
        return div;
    }

    function showTooltip(idx, mx, my) {
        var task = tasks[idx];
        while (tipEl.firstChild) tipEl.removeChild(tipEl.firstChild);

        var title = document.createElement("div");
        // 主标题用当前语言的任务名（如 活动图-2+），原始命令名灰字附在后面便于排查
        title.textContent = task.name;
        if (task.fullname && task.fullname !== task.name) {
            var raw = document.createElement("span");
            raw.textContent = " (" + task.fullname + ")";
            raw.style.opacity = "0.55";
            raw.style.fontWeight = "400";
            title.appendChild(raw);
        }
        title.style.fontWeight = "600";
        title.style.marginBottom = "2px";
        tipEl.appendChild(title);

        var sub = document.createElement("div");
        sub.textContent = task.time + (task.events ? " · " + TXT_TIMES.replace("{n}", task.events) : "");
        sub.style.opacity = "0.7";
        sub.style.marginBottom = "4px";
        tipEl.appendChild(sub);

        if (task.gains.length) {
            tipEl.appendChild(sectionHeader("▲ " + TXT_GAIN, PAL.gain));
            task.gains.forEach(function (g) {
                tipEl.appendChild(tooltipRow(g.name, g.color, "+" + fmtVal(g.value), PAL.gain));
            });
        }
        if (task.losses.length) {
            tipEl.appendChild(sectionHeader("▼ " + TXT_LOSS, PAL.loss));
            task.losses.forEach(function (l) {
                tipEl.appendChild(tooltipRow(l.name, l.color, "-" + fmtVal(l.value), PAL.loss));
            });
        }

        tipEl.style.display = "block";
        var tw = tipEl.offsetWidth, th = tipEl.offsetHeight;
        var tx = mx + 16, ty = my - th - 12;
        if (tx + tw > W - 8) tx = mx - tw - 16;
        if (ty < 8) ty = my + 16;
        tipEl.style.left = Math.max(4, tx) + "px";
        tipEl.style.top = Math.max(4, ty) + "px";
    }

    function hideTooltip() {
        tipEl.style.display = "none";
    }

    function handleHover(e) {
        var rect = cv.getBoundingClientRect();
        var mx = e.clientX - rect.left;
        var my = e.clientY - rect.top;
        var idx = nodeAtX(mx);
        if (idx === null) {
            hideTooltip();
            if (hoverIdx !== null) { hoverIdx = null; draw(); }
            return;
        }
        hoverIdx = idx;
        draw();
        showTooltip(idx, mx, my);
    }

    // ---- 交互：拖动平移 / 点按查看 / 滚轮缩放 ----

    var dragging = false, dragMoved = false, dragStartX = 0, dragStartFirst = 0;

    addListener(cv, "pointerdown", function (e) {
        dragging = true;
        dragMoved = false;
        dragStartX = e.clientX;
        dragStartFirst = firstVisible;
        if (cv.setPointerCapture) {
            try { cv.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
        }
    });

    addListener(cv, "pointermove", function (e) {
        if (dragging) {
            var dx = e.clientX - dragStartX;
            if (Math.abs(dx) > 5) dragMoved = true;
            if (dragMoved) {
                firstVisible = Math.round(dragStartFirst - dx / colW);
                clampPan();
                hoverIdx = null;
                hideTooltip();
                draw();
            }
        } else if (e.pointerType === "mouse") {
            handleHover(e);
        }
    });

    addListener(cv, "pointerup", function (e) {
        if (dragging && !dragMoved) {
            // 点按（含触屏）视为查看该节点详情
            handleHover(e);
        }
        dragging = false;
    });

    addListener(cv, "pointerleave", function () {
        dragging = false;
        hoverIdx = null;
        hideTooltip();
        draw();
    });

    addListener(cv, "wheel", function (e) {
        e.preventDefault();
        var step = e.deltaY > 0 ? 2 : -2;
        var rect = cv.getBoundingClientRect();
        var mx = e.clientX - rect.left;
        var anchor = nodeAtX(mx);
        var oldColW = colW;
        var newCount = Math.max(1, Math.min(n, visibleCount + step));
        if (newCount === visibleCount) return;
        visibleCount = newCount;
        colW = gW / visibleCount;
        if (anchor !== null) {
            // 以指针所在节点为锚点缩放：保持其屏幕像素位置不变
            var anchorPx = (anchor - firstVisible) * oldColW;
            firstVisible = Math.round(anchor - anchorPx / colW);
        }
        clampPan();
        draw();
    }, { passive: false });

    function bindButton(id, fn) {
        var btn = document.getElementById(chartId + "_" + id);
        if (btn) addListener(btn, "click", fn);
    }
    bindButton("zoom_in", function () {
        visibleCount = Math.max(1, visibleCount - 2);
        colW = gW / visibleCount;
        clampPan();
        draw();
    });
    bindButton("zoom_out", function () {
        visibleCount = Math.min(n, visibleCount + 2);
        colW = gW / visibleCount;
        clampPan();
        draw();
    });
    bindButton("reset", function () {
        visibleCount = defaultVisible;
        colW = gW / visibleCount;
        firstVisible = 0;
        clampPan();
        draw();
    });

    function fmtVal(v) {
        if (v === null || v === undefined) return "-";
        if (typeof v === "number") {
            if (Number.isInteger(v)) return v.toLocaleString();
            return v.toFixed(1);
        }
        return String(v);
    }

    // 延迟渲染，等待布局稳定（与既有图表行为一致）
    initialRenderTimer = setTimeout(function () {
        initialRenderTimer = null;
        readPalette();
        computeLayout();
        clampPan();
        draw();
    }, 300);
})();
