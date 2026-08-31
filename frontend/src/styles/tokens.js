/**
 * 法律助手 Demo — Ant Design 主题 token 配置
 * 与 design-system.css 中的 CSS 变量保持同步。
 * 用法：在 main.jsx 中替换原有 themeConfig，改为：
 *   import { getThemeConfig } from './styles/tokens.js'
 *   <ConfigProvider theme={getThemeConfig(dark)} ... />
 */

// 浅色主题：专业、克制的法律科技风格
const LIGHT_TOKEN = {
  colorPrimary: '#1e5aa8',
  colorPrimaryHover: '#164785',
  colorPrimaryActive: '#0f3561',
  colorPrimaryBg: '#e8f0f8',
  colorPrimaryBgHover: '#d6e5f4',
  colorPrimaryBorder: '#b8d3ee',

  colorBgLayout: '#f3f4f6',
  colorBgContainer: '#ffffff',
  colorBgElevated: '#ffffff',

  colorBorder: 'rgba(0, 0, 0, 0.08)',
  colorBorderSecondary: 'rgba(0, 0, 0, 0.06)',
  colorSplit: 'rgba(0, 0, 0, 0.06)',

  colorText: '#111827',
  colorTextSecondary: '#4b5563',
  colorTextTertiary: '#9ca3af',

  colorFill: 'rgba(0, 0, 0, 0.04)',
  colorFillSecondary: 'rgba(0, 0, 0, 0.02)',
  colorFillTertiary: 'rgba(0, 0, 0, 0.04)',
  colorFillQuaternary: 'rgba(0, 0, 0, 0.02)',

  borderRadius: 8,
  borderRadiusLG: 12,
  borderRadiusSM: 6,
  borderRadiusXS: 4,

  fontSize: 14,
  fontSizeSM: 12,
  lineHeight: 1.7,

  // 自定义 token，供组件内 theme.useToken() 读取
  sidebarBg: '#f8fafc',
  highlightSearch: '#fde68a',
  highlightScan: '#bfdbfe',
  colorAmber: '#d97706',
}

// 深色主题：保持足够的对比度，避免纯黑
const DARK_TOKEN = {
  colorPrimary: '#4a9eff',
  colorPrimaryHover: '#74b4ff',
  colorPrimaryActive: '#93c5fd',
  colorPrimaryBg: '#142845',
  colorPrimaryBgHover: '#1c3a63',
  colorPrimaryBorder: '#2a4d7a',

  colorBgLayout: '#0f172a',
  colorBgContainer: '#1e293b',
  colorBgElevated: '#27354f',

  colorBorder: 'rgba(255, 255, 255, 0.08)',
  colorBorderSecondary: 'rgba(255, 255, 255, 0.06)',
  colorSplit: 'rgba(255, 255, 255, 0.08)',

  colorText: '#f1f5f9',
  colorTextSecondary: '#94a3b8',
  colorTextTertiary: '#64748b',

  colorFill: 'rgba(255, 255, 255, 0.08)',
  colorFillSecondary: 'rgba(255, 255, 255, 0.04)',
  colorFillTertiary: 'rgba(255, 255, 255, 0.06)',
  colorFillQuaternary: 'rgba(255, 255, 255, 0.04)',

  borderRadius: 8,
  borderRadiusLG: 12,
  borderRadiusSM: 6,
  borderRadiusXS: 4,

  fontSize: 14,
  fontSizeSM: 12,
  lineHeight: 1.7,

  sidebarBg: '#0b1220',
  highlightSearch: 'rgba(251, 191, 36, 0.25)',
  highlightScan: 'rgba(74, 158, 255, 0.25)',
  colorAmber: '#fbbf24',
}

/**
 * 生成 Ant Design ConfigProvider 主题配置
 * @param {boolean} dark 是否深色模式
 * @returns {import('antd').ThemeConfig}
 */
export function getThemeConfig(dark) {
  const token = dark ? DARK_TOKEN : LIGHT_TOKEN
  return {
    algorithm: dark ? undefined : undefined,
    token,
    components: {
      Layout: {
        siderBg: token.sidebarBg,
        headerBg: token.colorBgLayout,
        bodyBg: token.colorBgLayout,
      },
      Menu: {
        itemBg: token.sidebarBg,
        itemSelectedBg: token.colorPrimaryBg,
        itemSelectedColor: token.colorPrimary,
        popupBg: token.colorBgElevated,
      },
      Card: {
        colorBgContainer: token.colorBgContainer,
        borderRadiusLG: token.borderRadiusLG,
        paddingLG: 16,
      },
      Button: {
        borderRadius: token.borderRadius,
        primaryShadow: 'none',
      },
      Input: {
        borderRadius: token.borderRadius,
      },
      Tabs: {
        colorBgContainer: token.colorBgContainer,
      },
      List: {
        colorSplit: token.colorSplit,
      },
    },
  }
}

export { LIGHT_TOKEN, DARK_TOKEN }
