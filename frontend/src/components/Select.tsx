import { useEffect, useId, useLayoutEffect, useRef, useState, type ComponentProps, type KeyboardEvent } from 'react'
import { Check, ChevronsUpDown } from 'lucide-react'
import { useApp } from '../app/context'

type Option = {value: string; label: string; disabled: boolean}

/** 自绘列表通过顶层弹出层避开卡片裁切；原生节点仅用于值与变更事件桥接。 */
export function Select({children, id, className, style, disabled, autoFocus, ...props}: ComponentProps<'select'>) {
  const {ui} = useApp()
  const generatedId = useId()
  const controlId = id ?? generatedId
  const listId = `${controlId}-options`
  const native = useRef<HTMLSelectElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const popup = useRef<HTMLDivElement>(null)
  const [options, setOptions] = useState<Option[]>([])
  const [selected, setSelected] = useState(-1)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const search = useRef({text: '', time: 0})

  useLayoutEffect(() => {
    const select = native.current!
    setOptions(Array.from(select.options, option => ({value: option.value, label: option.label, disabled: option.disabled || (option.parentElement instanceof HTMLOptGroupElement && option.parentElement.disabled)})))
    setSelected(select.selectedIndex)
  }, [children, props.value, props.defaultValue])

  useEffect(() => {if (disabled) setOpen(false)}, [disabled])

  useLayoutEffect(() => {
    if (!open) return
    const menu = popup.current!
    // 顶层弹出层仍保留在原 DOM 内，兼容 dialog 的焦点约束和标签名称。
    menu.showPopover()
    const position = () => {
      const rect = trigger.current!.getBoundingClientRect()
      const viewport = window.visualViewport
      const left = viewport?.offsetLeft ?? 0
      const top = viewport?.offsetTop ?? 0
      const width = viewport?.width ?? innerWidth
      const height = viewport?.height ?? innerHeight
      const below = Math.max(0, top + height - rect.bottom - 12)
      const above = Math.max(0, rect.top - top - 12)
      const upwards = below < Math.min(280, menu.scrollHeight) && above > below
      const space = upwards ? above : below
      const menuWidth = Math.min(Math.max(rect.width, 180), width - 16)
      // 弹出层不继承页面缩放，使用视口坐标定位。
      menu.style.width = `${menuWidth}px`
      menu.style.maxHeight = `${Math.min(320, space)}px`
      menu.style.left = `${Math.max(left + 8, Math.min(rect.left, left + width - menuWidth - 8))}px`
      menu.style.top = `${upwards ? rect.top - menu.getBoundingClientRect().height - 6 : rect.bottom + 6}px`
    }
    position()
    const outside = (event: PointerEvent) => {
      if (!menu.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) setOpen(false)
    }
    const scroll = (event: Event) => {if (!menu.contains(event.target as Node)) position()}
    document.addEventListener('pointerdown', outside, true)
    window.addEventListener('scroll', scroll, true)
    window.addEventListener('resize', position)
    window.visualViewport?.addEventListener('resize', position)
    window.visualViewport?.addEventListener('scroll', position)
    const observer = new ResizeObserver(position)
    observer.observe(trigger.current!)
    return () => {
      menu.hidePopover()
      observer.disconnect()
      document.removeEventListener('pointerdown', outside, true)
      window.removeEventListener('scroll', scroll, true)
      window.removeEventListener('resize', position)
      window.visualViewport?.removeEventListener('resize', position)
      window.visualViewport?.removeEventListener('scroll', position)
    }
  }, [open, options])

  useLayoutEffect(() => {
    if (!open) return
    const menu = popup.current!
    const option = menu.querySelector<HTMLElement>(`[data-index="${active}"]`)
    if (!option) return
    // 只滚动选项列表，避免 scrollIntoView 连带移动背景页面。
    if (option.offsetTop < menu.scrollTop) menu.scrollTop = option.offsetTop
    else if (option.offsetTop + option.offsetHeight > menu.scrollTop + menu.clientHeight) menu.scrollTop = option.offsetTop + option.offsetHeight - menu.clientHeight
  }, [open, active, options])

  function show() {
    if (disabled || !options.length) return
    search.current = {text: '', time: 0}
    setActive(selected >= 0 && !options[selected]?.disabled ? selected : options.findIndex(option => !option.disabled))
    setOpen(true)
  }

  function choose(index: number) {
    const option = options[index]
    if (!option || option.disabled) return
    setOpen(false)
    trigger.current?.focus({preventScroll: true})
    if (native.current!.selectedIndex === index) return
    native.current!.selectedIndex = index
    native.current!.dispatchEvent(new Event('change', {bubbles: true}))
    setSelected(native.current!.selectedIndex)
  }

  function keyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === 'Tab') {setOpen(false); return}
    if (event.key === 'Escape' && open) {event.preventDefault(); event.stopPropagation(); setOpen(false); return}
    if (['Enter', ' '].includes(event.key)) {
      event.preventDefault()
      if (open) choose(active)
      else show()
      return
    }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault()
      const available = options.map((option, index) => option.disabled ? -1 : index).filter(index => index >= 0)
      if (!available.length) return
      if (!open) {show(); if (event.key === 'Home') setActive(available[0]); if (event.key === 'End') setActive(available.at(-1)!); return}
      const current = available.indexOf(active)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? available.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + available.length) % available.length
      setActive(available[next])
      return
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      const now = Date.now()
      const text = now - search.current.time > 700 ? event.key : search.current.text + event.key
      search.current = {text, time: now}
      const prefix = new Set(text.toLowerCase()).size === 1 ? text[0] : text
      const start = open ? active : selected
      const index = options.findIndex((_, offset) => {
        const option = options[(start + offset + 1 + options.length) % options.length]
        return !option.disabled && option.label.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase())
      })
      if (index >= 0) {
        setActive((start + index + 1 + options.length) % options.length)
        setOpen(true)
      }
    }
  }

  return <span className={`select-control ${className ?? ''}`} style={style}>
    <button ref={trigger} id={controlId} type="button" role="combobox" className="select-trigger" disabled={disabled} autoFocus={autoFocus}
      aria-label={props['aria-label']} aria-labelledby={props['aria-labelledby']} aria-describedby={props['aria-describedby']}
      aria-invalid={props['aria-invalid']} aria-required={props.required} aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined}
      aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
      onClick={() => open ? setOpen(false) : show()} onKeyDown={keyDown} onBlur={() => setOpen(false)}>
      <span>{options[selected]?.label ?? ui('common.select')}</span><ChevronsUpDown size={15} aria-hidden="true"/>
    </button>
    <select {...props} ref={native} disabled={disabled} hidden aria-label={undefined} aria-labelledby={undefined} aria-hidden="true" tabIndex={-1}>{children}</select>
    {open && <div ref={popup} id={listId} className="select-menu" role="listbox" aria-labelledby={controlId} popover="manual" onPointerDown={event => event.preventDefault()}>
      {options.map((option, index) => <div key={`${index}-${option.value}`} id={`${listId}-${index}`} data-index={index} role="option" aria-selected={index === selected} aria-disabled={option.disabled || undefined}
        className={`select-option ${index === active ? 'is-active' : ''}`} onPointerMove={() => {if (!option.disabled) setActive(index)}} onClick={event => {event.preventDefault(); event.stopPropagation(); choose(index)}}>
        <Check size={15} aria-hidden="true" style={{visibility: index === selected ? 'visible' : 'hidden'}}/><span>{option.label}</span>
      </div>)}
    </div>}
  </span>
}
