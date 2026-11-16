// 配置页：**严格照抄老 UI 的 DOM 契约**，样式全部由老 CSS 提供。
//
// 老 UI 的真实结构（module/webui/app_task_config.py:_build_group_content +
// widgets.py:put_arg_*，逐行核对过）：
//
//   #pywebio-scope-content
//     └─ #pywebio-scope-_groups
//          ├─ #pywebio-scope-groups
//          │    └─ #pywebio-scope-group_{组名}          ← 卡片，CSS 靠这个 ID 选中
//          │         ├─ <p>组名</p>
//          │         ├─ <p>组帮助</p>（有才输出）
//          │         ├─ <hr class="hr-group">
//          │         └─ #pywebio-scope-arg_container-XXX-{参数名}
//          │              ├─ <p style="--arg-title--">标题</p>
//          │              ├─ <p style="--arg-help--">帮助</p>（有才输出）
//          │              └─ 控件（.form-control / select）
//          └─ #pywebio-scope-navigator                   ← 右侧分组导航
//
// 关键：**不要在 tsx 里写任何外观样式**。alas.css 的规则全部建立在
// 这些 ID 与 --arg-*-- 钩子上，用它们就等于自动获得老 UI 的外观。
// 之前我自造 .ov-card / .cfg-row 等类，老 CSS 不认识，只能自己补样式，
// 结果盖在老 CSS 上造成白光与挤压。
//
// 另外：老 UI 的「分组懒渲染」是 PyWebIO 的妥协（服务端逐个 scope 推送），
// React 一次渲染完即可，这里**不做懒渲染、不做骨架屏**。
import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { Shell } from '../../components/Shell'

type ArgOption = { label: string; value: string }
type Arg = {
  name: string; title: string; help: string;
  type: string; value: any; option: ArgOption[]
}
type Group = { group: string; title: string; args: Arg[] }
type ConfigResp = { task: string; groups: Group[]; error?: string }

/** 控件：与 widgets.py 的 put_arg_* 分支一一对应。 */
function ArgControl({ arg, onChange }: { arg: Arg; onChange: (v: any) => void }) {
  // put_arg_select：<select class="form-control">（.style("--input--") 由外层 scope 承载）
  if (arg.type === 'select' && arg.option.length) {
    return (
      <select
        className="form-control"
        value={arg.value ?? ''}
        onChange={e => onChange(e.target.value)}
      >
        {arg.option.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    )
  }
  // put_arg_checkbox：Bootstrap 自定义开关
  if (arg.type === 'checkbox' || typeof arg.value === 'boolean') {
    return (
      <div className="form-check">
        <input
          className="form-check-input"
          type="checkbox"
          checked={!!arg.value}
          onChange={e => onChange(e.target.checked)}
        />
      </div>
    )
  }
  // put_arg_textarea
  if (arg.type === 'textarea') {
    return (
      <textarea
        className="form-control"
        rows={3}
        value={arg.value ?? ''}
        onChange={e => onChange(e.target.value)}
      />
    )
  }
  // put_arg_input（含 datetime，值为字符串）
  return (
    <input
      className="form-control"
      type="text"
      value={arg.value ?? ''}
      onChange={e => onChange(e.target.value)}
    />
  )
}

/** 单个参数块：#pywebio-scope-arg_container-{类型}-{参数名}。 */
function ArgBlock({ arg, onChange }: { arg: Arg; onChange: (v: any) => void }) {
  const kind =
    arg.type === 'select' ? 'select'
    : arg.type === 'checkbox' || typeof arg.value === 'boolean' ? 'checkbox'
    : arg.type === 'textarea' ? 'textarea'
    : 'input'
  return (
    <div id={`pywebio-scope-arg_container-${kind}-${arg.name}`}>
      {/* ⚠️ 结构必须与 widgets.py:get_title_help 一致：
          标题与帮助包在一个容器里，整体算 **一个** grid 项。
          alas-pc.css 的 [id^="pywebio-scope-arg_container-"] 是
            grid-auto-flow: column; grid-template-columns: 1fr 13rem
          —— 恰好两列（左：标题+帮助，右：控件）。
          我原来把标题、帮助、控件作为 3 个直接子元素，被塞进 2 列 grid，
          于是标题占第 1 列、帮助占第 2 列（13rem 窄栏里换行成竖排）、
          控件被挤到下一行 —— 长卿截图里「歪七扭八」就是这个原因。
          标题/帮助用内联 style 钩子（alas.css:1060 是 *[style*="--arg-title--"]
          子串匹配，React 的 style 对象写不出这种标记，只能自己拼 style 属性）。 */}
      <div style={{ display: 'grid', gridAutoFlow: 'row', gridTemplateRows: 'auto 1fr' }}>
        <p style={{ whiteSpace: 'pre-wrap', ['--arg-title--' as string]: '1' } as React.CSSProperties}>
          {arg.title}
        </p>
        {arg.help && (
          <p style={{ whiteSpace: 'pre-wrap', ['--arg-help--' as string]: '1' } as React.CSSProperties}>
            {arg.help}
          </p>
        )}
      </div>
      <div style={{ ['--input--' as string]: '1' } as React.CSSProperties}>
        <ArgControl arg={arg} onChange={onChange} />
      </div>
    </div>
  )
}

export function ConfigPage() {
  const { name = 'alas', task = '' } = useParams()
  const [groups, setGroups] = useState<Group[]>([])
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    setLoaded(false)
    apiGet<ConfigResp>(`/newapi/config/${name}/task/${task}`).then(r => {
      if (r.error) setError(r.error)
      setGroups(r.groups)
    }).catch(e => setError(String(e))).finally(() => setLoaded(true))
  }, [name, task])

  const setArg = (group: string, argName: string, value: any) => {
    setGroups(gs => gs.map(g => g.group !== group ? g : {
      ...g,
      args: g.args.map(a => a.name !== argName ? a : { ...a, value }),
    }))
    scheduleSave({ [`${group}.${argName}`]: value })
  }

  // 老 UI 配置页没有「保存」按钮 —— 每个控件改完由 pin_on_change 自动落盘。
  // 新 UI 用 debounce 自动保存，交互与老 UI 一致。
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingRef = useRef<Record<string, any>>({})
  const scheduleSave = (patch: Record<string, any>) => {
    pendingRef.current = { ...pendingRef.current, ...patch }
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => { void save() }, 600)
  }

  // 自动保存：与老 UI 的 pin_on_change 同语义（改完即落盘，无保存按钮）
  const save = async () => {
    const batch = pendingRef.current
    if (!Object.keys(batch).length) return
    pendingRef.current = {}
    try {
      const res = await fetch(`/newapi/config/${name}/task/${task}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ modified: batch }),
      })
      const j = await res.json()
      if (j.invalid?.length) setError(`${j.valid?.length ?? 0} 项已保存，${j.invalid.length} 项校验失败`)
      else setError('')
    } catch (e) { setError('保存失败: ' + String(e)) }
  }

  // 右侧分组导航（老 UI #pywebio-scope-navigator 同构）
  const navItems = useMemo(
    () => groups.map(g => ({ id: `pywebio-scope-group_${g.group}`, name: g.title || g.group })),
    [groups],
  )

  return (
    <Shell active={task} title="配置">
      {/* #pywebio-scope-content 里**只能有 #pywebio-scope-_groups**：
          老 CSS 中 contents 是 display:flex，_groups 是 height:100% + grid 三列。
          多塞任何兄弟节点都会把 _groups 挤歪（实测卡片宽度塌成 33px）。
          老 UI 也没有保存按钮 —— 控件改完由 pin_on_change 自动落盘，
          这里用 600ms debounce 自动保存保持一致。 */}
      {error && <p style={{ color: 'var(--alas-entry-badge-error, #c0392b)', margin: '.5rem .625rem' }}>{error}</p>}
      <div id="pywebio-scope-content">
        <div id="pywebio-scope-_groups">
          {/* 占位：老 UI 是 put_none()，对应 grid 的**第一列 0 宽**。
              #pywebio-scope-_groups 的 grid-template-columns 是
              "0 minmax(25rem,5fr) 1fr" —— 没有这个占位，
              groups 会落到第 1 列（宽 0），卡片宽度塌成 33px。 */}
          <div />
          <div id="pywebio-scope-groups">
            {groups.map(g => (
              <div key={g.group} id={`pywebio-scope-group_${g.group}`}>
                <p style={{ whiteSpace: 'pre-wrap' }}>{g.title || g.group}</p>
                <hr className="hr-group" />
                <div id={`pywebio-scope-group_${g.group}_body`}>
                  {g.args.map(a => (
                    <ArgBlock
                      key={a.name}
                      arg={a}
                      onChange={v => setArg(g.group, a.name, v)}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div id="pywebio-scope-navigator">
            {navItems.map(it => (
              <button
                key={it.id}
                className="btn btn-menu"
                onClick={() => {
                  // 滚动定位（对应 _expand_config_group_and_scroll 的滚动部分）
                  const el = document.getElementById(it.id)
                  const box = document.getElementById('pywebio-scope-groups')
                  if (el && box) box.scrollTop = el.offsetTop - 59
                }}
              >
                {it.name}
              </button>
            ))}
          </div>
        </div>
        {!loaded && <p style={{ margin: '.5rem', opacity: .7 }}>加载中…</p>}
      </div>
    </Shell>
  )
}
