// 任务集合页的实例定义。
//
// 长卿 2026-11-20 两次要求：
//   ① 其他配置：共斗活动/共斗沉船/深谷来信/夜勤病栋/联盟沉船/商路护航/作战档案
//   ② 主线图 = 主线图-1/2/3
//      紧急委托 = 紧急委托/紧急委托-2/三油低耗/1-1刷伏击
//      活动图 = 活动图-1/2/3
//      活动每日 = 每日A/B/C/D/SP图 + 共斗活动每日 + 怪谈纪实SP
//
// 每个集合在菜单里只占一行（后端 get_menu 里插入 CollectionEntry 假任务项），
// 点进去后顶部页签切换。各任务的配置本身完全不变。
import { TaskCollectionPage, type TaskCollection } from './TaskCollectionPage'

// ① 其他配置（活动分组里收纳的 7 个任务）
const OTHER_CONFIG: TaskCollection = {
  route: 'config-other',
  title: '其他配置',
  desc: '这些任务的配置项都在，用下面的页签切换。',
  tabs: [
    { id: 'Raid', label: '共斗活动' },
    { id: 'RaidScuttle', label: '共斗沉船' },
    { id: 'Hospital', label: '深谷来信' },
    { id: 'Coalition', label: '夜勤病栋' },
    { id: 'CoalitionScuttle', label: '联盟沉船' },
    { id: 'MaritimeEscort', label: '商路护航' },
    { id: 'WarArchives', label: '作战档案' },
  ],
}

// ② 主线图（出击分组）
const MAIN_CHAPTERS: TaskCollection = {
  route: 'main-chapters',
  title: '主线图',
  desc: '主线图-1 / -2 / -3 的配置，用下面的页签切换。',
  tabs: [
    { id: 'Main', label: '主线图-1' },
    { id: 'Main2', label: '主线图-2' },
    { id: 'Main3', label: '主线图-3' },
  ],
}

// ③ 紧急委托（出击分组）
const GEMS_FARMING: TaskCollection = {
  route: 'gems-farming',
  title: '紧急委托',
  desc: '紧急委托与低耗刷图任务的配置，用下面的页签切换。',
  tabs: [
    { id: 'GemsFarming', label: '紧急委托' },
    { id: 'GemsFarming2', label: '紧急委托-2' },
    { id: 'ThreeOilLowCost', label: '三油低耗' },
    { id: 'Ambush11', label: '1-1刷伏击(尚不完善)' },
  ],
}

// ④ 活动图（活动分组）
const EVENT_STAGES: TaskCollection = {
  route: 'event-stages',
  title: '活动图',
  desc: '活动图-1 / -2 / -3 的配置，用下面的页签切换。',
  tabs: [
    { id: 'Event', label: '活动图-1' },
    { id: 'Event2', label: '活动图-2' },
    { id: 'Event3', label: '活动图-3' },
  ],
}

// ⑤ 活动每日（活动-每日任务分组）
const EVENT_DAILY: TaskCollection = {
  route: 'event-daily',
  title: '活动每日',
  desc: '每日活动图的配置，用下面的页签切换。',
  tabs: [
    { id: 'EventA', label: '每日A图' },
    { id: 'EventB', label: '每日B图' },
    { id: 'EventC', label: '每日C图' },
    { id: 'EventD', label: '每日D图' },
    { id: 'EventSp', label: '每日SP图' },
    { id: 'RaidDaily', label: '共斗活动每日' },
    { id: 'CoalitionSp', label: '怪谈纪实：逃离白夜山庄SP' },
  ],
}

// ── 五个路由组件 ──
export function OtherConfigPage() { return <TaskCollectionPage collection={OTHER_CONFIG} /> }
export function MainChaptersPage() { return <TaskCollectionPage collection={MAIN_CHAPTERS} /> }
export function GemsFarmingPage() { return <TaskCollectionPage collection={GEMS_FARMING} /> }
export function EventStagesPage() { return <TaskCollectionPage collection={EVENT_STAGES} /> }
export function EventDailyPage() { return <TaskCollectionPage collection={EVENT_DAILY} /> }

/** 所有集合页的 route → 定义，供菜单跳转与旧地址重定向查表 */
export const COLLECTIONS: Record<string, TaskCollection> = {
  'config-other': OTHER_CONFIG,
  'main-chapters': MAIN_CHAPTERS,
  'gems-farming': GEMS_FARMING,
  'event-stages': EVENT_STAGES,
  'event-daily': EVENT_DAILY,
};

/** 任务 key → 所属集合 route（旧地址 /config/{name}/{task} 重定向用） */
export const TASK_TO_COLLECTION: Record<string, string> = (() => {
  const m: Record<string, string> = {};
  for (const [route, col] of Object.entries(COLLECTIONS)) {
    for (const t of col.tabs) m[t.id] = route;
  }
  return m;
})();