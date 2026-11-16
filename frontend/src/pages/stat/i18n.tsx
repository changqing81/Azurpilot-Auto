// 统计页文案（老 UI i18n 原文）。
//
// 长卿：「文案一定要是老UI的文案，不要乱改」
//
// 文案来源：后端 /newapi/stat/i18n 按当前界面语言返回 Gui.Stat.* /
// Gui.FleetTechnology.* 的**原文**（71 个键，键名 = 老 i18n 键去前缀）。
// 前端任何组件**不允许自己写中文文案** —— 以前到处硬编码（「科研」
// 「加载中…」「科技功能」「扫描范围」…），切英文后统计页还是中文，
// 而且有些字面值与老 UI 不一致（「期数」vs「期数视图」）。
import { createContext, useContext } from 'react'

export type StatI18n = Record<string, string>

/** 缺失时的兜底：与 zh-CN 原文一致（老后端没有 /newapi/stat/i18n 时用）。 */
export const STAT_I18N_FALLBACK: StatI18n = {
  PageTitle: '统计',
  Refresh: '刷新',
  TabOpsi: '大世界',
  TabCommission: '委托',
  TabResearch: '科研',
  TabResource: '资源',
  TabFleet: '科技列表',
  TabMind: '心智计算器',
  ApChartTitle: '体力变化',
  ViewLineButton: '折线',
  ViewDayButton: '按日',
  ViewMonthButton: '按月',
  ResearchScopeSeries: '期数视图',
  ResearchScopeConsumable: '心智 / 物资',
  ResearchPeriodMonth: '本月',
  ResearchPeriodDays30: '近 30 天',
  ResearchPeriodDays90: '近 90 天',
  ResearchPeriodAll: '全部',
  ResearchMetricTotal: '总掉落',
  ResearchMetricToday: '今日',
  ResearchMetricMonth: '本月',
  ResearchMetricRecords: '记录次数',
  DeltaPeriodDay: '日',
  DeltaPeriodWeek: '周',
  DeltaPeriodMonth: '月',
  DeltaPeriodTotal: '总',
  FtCategory: '类别',
  FtRarity: '稀有度',
  FtNation: '阵营/分组',
  FtBonusType: '加成舰种',
  FtAttr: '加成属性',
  FtStatus: '完成情况',
  FtFunctions: '科技功能',
}

export const StatI18nContext = createContext<StatI18n>(STAT_I18N_FALLBACK)

/** 取文案：优先后端值，键缺失时回退 zh-CN 兜底表。 */
export function useStatText(): (key: string) => string {
  const dict = useContext(StatI18nContext)
  return (key: string) => dict[key] || STAT_I18N_FALLBACK[key] || key
}