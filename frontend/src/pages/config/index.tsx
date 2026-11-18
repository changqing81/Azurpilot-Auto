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
import { ArgBlock, type Group } from './groups'

type ConfigResp = { task: string; groups: Group[]; error?: string }

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

  // 自动保存：与老 UI 的 pin_on_change 同语义（改完即落盘，无保存按钮）。
  // 非 200 时先报 HTTP 状态，不要让 JSON.parse 的 SyntaxError 盖掉真实原因。
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
      if (!res.ok) { setError(`保存失败: HTTP ${res.status}`); return }
      const j = await res.json().catch(() => null)
      if (!j) { setError('保存失败: 后端返回了无法解析的内容'); return }
      if (j.error) { setError('保存失败: ' + j.error); return }
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
      {/* 错误条固定占位：条件渲染会改变整体高度 → 滚动条跳 → 页面弹一下。 */}
      <p style={{ color: 'var(--alas-entry-badge-error, #c0392b)', margin: '.5rem .625rem', minHeight: '1.2rem', visibility: error ? 'visible' : 'hidden' }}>{error || ' '}</p>
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
