import React, { useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import { App as AntdApp, ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App.jsx'
import './styles/design-system.css'

const THEME_KEY = 'app-shell-theme'

// App Shell UI 设计 token → antd token 映射
// 浅色：#F4F5F7 画布 / 白卡 / #1C1C1E 主文字 / #1677ff 法律蓝
const LIGHT_TOKEN = {
  colorPrimary: '#1e5aa8',
  colorBgLayout: '#F4F5F7',
  colorBgContainer: '#FFFFFF',
  colorBgElevated: '#FFFFFF',
  colorBorder: 'rgba(0, 0, 0, 0.08)',
  colorBorderSecondary: 'rgba(0, 0, 0, 0.06)',
  colorSplit: 'rgba(0, 0, 0, 0.06)',
  colorText: '#1C1C1E',
  colorTextSecondary: '#636366',
  colorTextTertiary: '#8E8E93',
  colorFillTertiary: 'rgba(0, 0, 0, 0.04)',
  colorFillQuaternary: 'rgba(0, 0, 0, 0.02)',
  borderRadius: 8,
  borderRadiusLG: 12,
  // 自定义：合同检索/扫描命中高亮
  highlightSearch: '#ffeb3b',
  highlightScan: '#bae7ff',
  // 自定义：侧栏底色（略深于画布，制造弱对比 seam）
  sidebarBg: '#EEF0F3',
}

// 深色：软炭色阶梯（非纯黑），#2C2C2E 抬升卡 / #3A3A3C 弹出层 / #4096ff 主色对
const DARK_TOKEN = {
  colorPrimary: '#4a9eff',
  colorBgLayout: '#1C1C1E',
  colorBgContainer: '#2C2C2E',
  colorBgElevated: '#3A3A3C',
  colorBorder: 'rgba(255, 255, 255, 0.08)',
  colorBorderSecondary: 'rgba(255, 255, 255, 0.06)',
  colorSplit: 'rgba(255, 255, 255, 0.08)',
  colorText: '#F5F5F7',
  colorTextSecondary: '#A1A1A6',
  colorTextTertiary: '#6C6C70',
  colorFillTertiary: 'rgba(255, 255, 255, 0.06)',
  colorFillQuaternary: 'rgba(255, 255, 255, 0.04)',
  borderRadius: 8,
  borderRadiusLG: 12,
  // 自定义：合同检索/扫描命中高亮（深色降饱和，保证文字可读）
  highlightSearch: 'rgba(255, 213, 79, 0.28)',
  highlightScan: 'rgba(64, 150, 255, 0.28)',
  // 自定义：侧栏底色（深色用 #161617，比画布更深）
  sidebarBg: '#161617',
}

function themeConfig(dark) {
  const token = dark ? DARK_TOKEN : LIGHT_TOKEN
  const components = dark
    ? {
        Layout: { siderBg: '#161617', headerBg: '#1C1C1E', bodyBg: '#1C1C1E' },
        Menu: { itemBg: '#161617', popupBg: '#3A3A3C' },
      }
    : {
        Layout: { siderBg: '#EEF0F3', headerBg: '#F4F5F7', bodyBg: '#F4F5F7' },
        Menu: { itemBg: '#EEF0F3', popupBg: '#FFFFFF' },
      }
  return {
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token,
    components,
  }
}

function getInitialDark() {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved === 'dark') return true
    if (saved === 'light') return false
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  } catch {
    return false
  }
}

function Root() {
  const [dark, setDark] = useState(getInitialDark)
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
  }, [dark])
  const onToggleTheme = () => {
    setDark((d) => {
      try { localStorage.setItem(THEME_KEY, d ? 'light' : 'dark') } catch {}
      return !d
    })
  }
  return (
    <ConfigProvider locale={zhCN} theme={themeConfig(dark)}>
      {/* antd App：提供上下文 message/notification/modal，使弹层随动态主题 */}
      <AntdApp component={false}>
        <App dark={dark} onToggleTheme={onToggleTheme} />
      </AntdApp>
    </ConfigProvider>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
)
