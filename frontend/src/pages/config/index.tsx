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
import { apiGet, instSeg } from '../../api/client'
import { Shell } from '../../components/Shell'
import { ArgBlock, type Group } from './groups'
import { EventCalculator } from './EventCalculator'
import { useToast } from '../../components/Toast'
type ConfigResp = { task: string; groups: Group[]; error?: string }
export function ConfigPage() {
  const { name = 'alas', task = '' } = useParams()
  const [groups, setGroups] = useState<Group[]>([])
  const toast = useToast()
  const [loaded, setLoaded] = useState(false)
  useEffect(() => {
    // task 为空时（路由参数丢失，如切实例后落在 /config/{name} 无任务段）不发请求：
    // 之前会请求 /newapi/config/{name}/task/ 打到 SPA 回退返回 HTML，JSON 解析失败；
    // 这里直接提示，等用户从菜单选任务。
    if (!task) { toast.error('请从左侧菜单选择一个任务'); setLoaded(true); return }
    setLoaded(false)
    apiGet<ConfigResp>(`/newapi/config/${instSeg(name)}/task/${encodeURIComponent(task)}`).then(r => {
      if (r.error) toast.error(r.error)
      setGroups(r.groups)
    }).catch(e => toast.error(String(e))).finally(() => setLoaded(true))
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
      const res = await fetch(`/newapi/config/${instSeg(name)}/task/${encodeURIComponent(task)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ modified: batch }),
      })
      if (!res.ok) { toast.error(`保存失败: HTTP ${res.status}`); return }
      const j = await res.json().catch(() => null)
      if (!j) { toast.error('保存失败: 后端返回异常'); return }
      if (j.error) { toast.error('保存失败: ' + j.error); return }
      // 老 UI：valid 项有 toast 成功提示（Gui.Toast.ConfigSaved「设置已保存」），
      // invalid 项给控件本身加红框（pin_set_invalid_mark），不弹整体错误。
      if (j.invalid?.length) {
        toast.error(`${j.invalid.length} 项格式不符，未保存`)
        // 给校验失败的控件加红框（对应老 UI pin_set_invalid_mark 的 is-invalid）。
        // DOM 容器 id 形如 arg_container-{widget}-{Group_Arg}，先把所有红框清掉再标。
        document.querySelectorAll('.config-invalid').forEach(el => el.classList.remove('config-invalid'))
        for (const k of j.invalid as string[]) {
          const parts = String(k).split('.')
          const key = parts.slice(-2).join('_')          // Group_Arg
          const el = Array.from(document.querySelectorAll('[id^="pywebio-scope-arg_container-"]'))
            .find(n => (n as HTMLElement).id.endsWith('-' + key))
          if (el) el.classList.add('config-invalid')
        }
      } else {
        if (j.valid?.length) toast.ok('设置已保存')
      }
    } catch (e) { toast.error('保存失败: ' + String(e)) }
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
                      // Scheduler.NextRun 带「立即运行」按钮：置空即触发调度器
                      // 立即执行（老 UI app_task_config.py:669-690 同款语义）。
                      onRunNow={
                        g.group === 'Scheduler' && a.name === 'NextRun'
                          ? () => {
                              setGroups(gs => gs.map(gg => gg.group !== g.group ? gg : {
                                ...gg,
                                args: gg.args.map(x => x.name !== a.name ? x : { ...x, value: '' }),
                              }))
                              pendingRef.current = { ...pendingRef.current, [`${g.group}.${a.name}`]: '' }
                              if (saveTimer.current) clearTimeout(saveTimer.current)
                              // 立即保存，不等 debounce：点一下就该跑
                              toast.ok('已触发立即运行')  // 老 UI：toast(t("Gui.Text.RunNow"))「立即运行」
                              void save()
                            }
                          : undefined
                      }
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
            {/* 活动计算器：老 UI app_task_config.py:487-489 挂在 EventGeneral 组之后。
                新 UI 无懒渲染，直接在组列表末尾渲染（EventGeneral 是该页最后一组前的位置；
                放在 navigator 前保持「属于 groups 列」的 DOM 层级，垫面规则才能命中。 */}
            {task === 'EventGeneral' && (
              <div id="pywebio-scope-group_EventCalculator">
                <p style={{ whiteSpace: 'pre-wrap' }}>活动计算器</p>
                <hr className="hr-group" />
                <EventCalculator name={name} />
              </div>
            )}
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