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
import { useNavigate, useParams } from 'react-router-dom'
import { apiGet, instSeg } from '../../api/client'
import { Shell } from '../../components/Shell'
import { ArgBlock, type Group } from './groups'
import { useToast } from '../../components/Toast'
type ConfigResp = { task: string; groups: Group[]; error?: string }

/**
 * 点击右侧分组导航后把目标分组滚到可视区。
 *
 * 为什么不能直接写 box.scrollTop：滚动容器随页面形态变化。
 *   - 独立配置页：`#pywebio-scope-groups` 有固定高度（`_groups` 是 height:100%
 *     的网格），内容溢出它自身滚动，能滚的就是 groups；
 *   - 集合页（其他配置/主线图/紧急委托/活动图/活动每日）：`newui-scoped.css`
 *     把 `.oc-body #pywebio-scope-_groups` 改成 height:auto，groups 被内容撑满，
 *     `scrollHeight === clientHeight` 没有可滚空间，实际滚动落在外层
 *     `#pywebio-scope-content` 上。
 * 2026-11-20 实测：集合页点导航 groupsTop 恒为 0（写死滚 groups 无效），
 * 外层 content 才是真正的滚动容器。
 *
 * 为什么不用 scrollIntoView：它会选中「最近的滚动祖先」就停手。集合页里
 * 最近的那个是 .oc-body 下的内层 #pywebio-scope-content（overflow-y:auto
 * 但被 .oc-glass 撑满、无可滚空间），于是外层容器永远不动 —— 实测无效。
 * 所以这里自己扫出「真的能滚」的容器，手算偏移再 scrollTo。
 * 手算用 getBoundingClientRect 差值而非 offsetTop：集合页多了
 * .oc-glass / .oc-tabs 等包装层，offsetTop 相对最近定位祖先，不再可直接用。
 */
function scrollToGroup(groupId: string) {
  const el = document.getElementById(groupId)
  if (!el) return
  // 找到第一个「真的能滚」的祖先容器。
  // 注意不能只看 overflowY：集合页里内层 #pywebio-scope-content 也是
  // overflow-y:auto，但被 .oc-glass 撑满（scrollHeight === clientHeight），
  // 它没有可滚空间，真正能滚的是更外层的 #pywebio-scope-content。
  let box: HTMLElement | null = el.parentElement
  let container: HTMLElement | null = null
  while (box && box !== document.body) {
    const style = getComputedStyle(box)
    const scrollStyle = style.overflowY === 'auto' || style.overflowY === 'scroll'
    if (scrollStyle && box.scrollHeight > box.clientHeight + 1) { container = box; break }
    box = box.parentElement
  }
  if (!container) return
  // 手算相对容器的偏移再设 scrollTop。
  // 不能用 scrollIntoView：它会选中「最近的滚动祖先」（集合页里就是那个
  // 滚不动的内层 content）并提前停手，外层容器永远不动 —— 2026-11-20 实测。
  const top = el.getBoundingClientRect().top
    - container.getBoundingClientRect().top
    + container.scrollTop
    - 16                                   // 顶部留白，分组标题不贴边
  container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
}

/** 任务 key → 所属集合页 route（旧地址 /config/{name}/{task} 重定向用） */
export const TASK_TO_COLLECTION: Record<string, string> = {
  // 其他配置
  Raid: 'config-other', RaidScuttle: 'config-other', Hospital: 'config-other',
  Coalition: 'config-other', CoalitionScuttle: 'config-other',
  MaritimeEscort: 'config-other', WarArchives: 'config-other',
  // 主线图
  Main: 'main-chapters', Main2: 'main-chapters', Main3: 'main-chapters',
  // 紧急委托
  GemsFarming: 'gems-farming', GemsFarming2: 'gems-farming',
  ThreeOilLowCost: 'gems-farming', Ambush11: 'gems-farming',
  // 活动图
  Event: 'event-stages', Event2: 'event-stages', Event3: 'event-stages',
  // 活动每日
  EventA: 'event-daily', EventB: 'event-daily', EventC: 'event-daily',
  EventD: 'event-daily', EventSp: 'event-daily', RaidDaily: 'event-daily',
  CoalitionSp: 'event-daily',
}
/**
 * 配置页外壳：从路由读 task，渲染单个任务的配置。
 *
 * 拆分原因（长卿 2026-11-20「其他配置」页签页）：
 * 原来的实现把路由参数 task 写死在组件里，无法在一个页面里切换多个任务。
 * 现在拆成 ConfigBody（接受 task 参数、可复用）+ ConfigPage（路由外壳）。
 */
export function ConfigPage() {
  const { name = 'alas', task = '' } = useParams()
  const navigate = useNavigate()
  // 被集合页收纳的任务：旧地址 /config/{name}/{task} 自动跳转并选中页签
  // （长卿 2026-11-20：旧地址要还能打开，体验无缝）
  const collectionRoute = TASK_TO_COLLECTION[task]
  useEffect(() => {
    if (collectionRoute) {
      navigate(
        '/' + collectionRoute + '/' + encodeURIComponent(name) + '?tab=' + encodeURIComponent(task),
        { replace: true },
      )
    }
  }, [name, task, navigate, collectionRoute])
  if (collectionRoute) return null
  return <ConfigBody name={name} task={task} />
}

/** 单任务配置主体。task 由调用方传入 —— 页签页可以复用它切换任务。 */
export function ConfigBody({
  name,
  task,
  hideShell = false,
}: {
  name: string
  task: string
  /** true 时不自带 Shell 外壳 —— 供页签页嵌在自己的 Shell 里 */
  hideShell?: boolean
}) {
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
  const body = (
    <>
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
                        {/* 活动计算器已移出本页（长卿 2026-11-20 定：独立成页）。
                原因：它挂在 #pywebio-scope-_groups 网格里，会把本页配置项挤到
                第一行只剩 227px。现在走侧栏「活动计算器」入口 →
                /event-calc/:name（见 pages/eventcalc/index.tsx）。 */}
          <div id="pywebio-scope-navigator">
            {navItems.map(it => (
              <button
                key={it.id}
                className="btn btn-menu"
                onClick={() => scrollToGroup(it.id)}
              >
                {it.name}
              </button>
            ))}
          </div>
        </div>
        {!loaded && <p style={{ margin: '.5rem', opacity: .7 }}>加载中…</p>}
      </div>
    </>
  )

  if (hideShell) return body
  // active 是**任务名**（配置树高亮用），实例名必须单独传：Shell 里所有菜单
  // 跳转都靠它拼 URL，缺了就会回退到实例列表第一个 —— 多实例用户在小号
  // 配置页点任何菜单项都会跳到 alas 的配置页（长卿 2026-11-25 实测）。
  return (
    <Shell active={task} instance={name} title="配置">
      {body}
    </Shell>
  )
}