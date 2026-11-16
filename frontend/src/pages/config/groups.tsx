// 配置分组渲染（从 ConfigPage 抽出的可复用部分）。
//
// 为什么抽出来：「工具」类任务（page=tool，如半自动点击/性能测试）在老 UI 里
// 走的是 alas_daemon_overview —— **和普通配置页用的是同一套 DOM 契约**，
// 只是页面顶部多一条调度器栏。所以配置卡的渲染必须共用一份，否则两边样式会漂。
//
// DOM 契约与 ConfigPage 完全一致（见 config/index.tsx 顶部说明）：
//   #pywebio-scope-group_{组名} > <p>标题</p> + <hr.hr-group> +
//   #pywebio-scope-arg_container-{类型}-{参数名}
import { useEffect, useRef, useState } from 'react'

export type ArgOption = { label: string; value: string }
export type Arg = {
  name: string; title: string; help: string;
  type: string; value: any; option: ArgOption[]
}
export type Group = { group: string; title: string; args: Arg[] }

/** 控件：与 widgets.py 的 put_arg_* 分支一一对应。 */
function ArgControl({ arg, onChange }: { arg: Arg; onChange: (v: any) => void }) {
  if (arg.type === 'select' && arg.option.length) {
    return (
      <select className="form-control" value={arg.value ?? ''} onChange={e => onChange(e.target.value)}>
        {arg.option.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    )
  }
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
  return (
    <input className="form-control" type="text" value={arg.value ?? ''} onChange={e => onChange(e.target.value)} />
  )
}

/** 单个参数块：#pywebio-scope-arg_container-{类型}-{参数名}。 */
export function ArgBlock({ arg, onChange }: { arg: Arg; onChange: (v: any) => void }) {
  const kind =
    arg.type === 'select' ? 'select'
    : arg.type === 'checkbox' || typeof arg.value === 'boolean' ? 'checkbox'
    : arg.type === 'textarea' ? 'textarea'
    : 'input'
  return (
    <div id={`pywebio-scope-arg_container-${kind}-${arg.name}`}>
      {/* 标题+帮助必须包在**一个** grid 项里：alas-pc.css 的
          [id^="pywebio-scope-arg_container-"] 是两列 grid（1fr 13rem），
          标题、帮助、控件若是 3 个直接子元素会被拆到 3 个格子里。 */}
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

/**
 * 渲染一组配置卡 + 自动保存。
 *
 * 老 UI 没有保存按钮——控件改完由 pin_on_change 自动落盘，这里用 600ms debounce 对齐。
 */
export function ConfigGroups({
  groups: initial, name, task, onError,
}: {
  groups: Group[]; name: string; task: string; onError?: (msg: string) => void
}) {
  const [groups, setGroups] = useState<Group[]>(initial)

  // 父组件异步拿到 schema 后要同步进来（首帧 initial 是空数组）。
  //
  // 依赖不能用 [initial]：父组件每次渲染都会重新 map 出**新数组引用**，
  // 于是这个 effect 每帧都跑、把用户刚改的值重置回服务端值 —— 表现是
  // 复选框点了没反应、下拉框弹回去。这里只在「内容签名真的变了」时同步。
  const sig = JSON.stringify(initial)
  const sigRef = useRef('')
  useEffect(() => {
    if (sigRef.current === sig) return
    sigRef.current = sig
    setGroups(initial)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig])

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingRef = useRef<Record<string, any>>({})

  const flush = async () => {
    const batch = pendingRef.current
    if (!Object.keys(batch).length) return
    pendingRef.current = {}
    try {
      const res = await fetch(`/newapi/config/${name}/task/${task}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ modified: batch }),
      })
      // ⚠️ 先判断 HTTP 状态再 parse：500 时后端返回 "Internal Server Error"
      // 纯文本，直接 res.json() 会抛 SyntaxError，把真实原因（HTTP 500）盖掉
      // —— 长卿截图里「Unexpected token 'I'」就是这么来的。
      if (!res.ok) {
        onError?.(`保存失败: HTTP ${res.status}`)
        return
      }
      const j = await res.json().catch(() => null)
      if (!j) {
        onError?.('保存失败: 后端返回了无法解析的内容')
        return
      }
      if (j.error) {
        onError?.('保存失败: ' + j.error)
        return
      }
      if (j.invalid?.length) {
        onError?.(`${j.valid?.length ?? 0} 项已保存，${j.invalid.length} 项校验失败`)
      } else {
        onError?.('')
      }
    } catch (e) {
      onError?.('保存失败: ' + String(e))
    }
  }

  const setArg = (group: string, argName: string, value: any) => {
    setGroups(gs => gs.map(g => g.group !== group ? g : {
      ...g,
      args: g.args.map(a => a.name !== argName ? a : { ...a, value }),
    }))
    pendingRef.current = { ...pendingRef.current, [`${group}.${argName}`]: value }
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => { void flush() }, 600)
  }

  return (
    <>
      {groups.map(g => (
        <div key={g.group} id={`pywebio-scope-group_${g.group}`}>
          <p style={{ whiteSpace: 'pre-wrap' }}>{g.title || g.group}</p>
          <hr className="hr-group" />
          <div id={`pywebio-scope-group_${g.group}_body`}>
            {g.args.map(a => (
              <ArgBlock key={a.name} arg={a} onChange={v => setArg(g.group, a.name, v)} />
            ))}
          </div>
        </div>
      ))}
    </>
  )
}