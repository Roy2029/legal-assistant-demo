import { theme, Typography } from 'antd'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const { Text } = Typography

// 页面页头：标题 + 弱化副标题（+ 右侧操作）
export function PageHeader({ title, subtitle, extra }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
      <div>
        <Typography.Title level={3} style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>{title}</Typography.Title>
        {subtitle && <Text type="secondary" style={{ fontSize: 13 }}>{subtitle}</Text>}
      </div>
      {extra && <div style={{ flexShrink: 0 }}>{extra}</div>}
    </div>
  )
}

// 主题感知的 Markdown 渲染：全局样式颜色全部取自 antd token，深色下可读
export function Markdown({ content, style }) {
  const { token } = theme.useToken()
  const css = `
    .md-body { word-break: break-word; color: ${token.colorText}; line-height: 1.6; }
    .md-body p { margin: 0 0 8px 0; }
    .md-body p:last-child { margin-bottom: 0; }
    .md-body h1, .md-body h2, .md-body h3, .md-body h4 { margin: 8px 0 6px 0; }
    .md-body ul, .md-body ol { margin: 4px 0 8px 0; padding-left: 20px; }
    .md-body table { border-collapse: collapse; width: 100%; margin: 8px 0; }
    .md-body th, .md-body td { border: 1px solid ${token.colorBorderSecondary}; padding: 4px 8px; font-size: 13px; }
    .md-body code { background: ${token.colorFillTertiary}; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
    .md-body pre { background: ${token.colorFillQuaternary}; border: 1px solid ${token.colorBorderSecondary}; padding: 8px 12px; border-radius: 6px; overflow: auto; font-size: 12px; }
    .md-body pre code { background: transparent; padding: 0; }
    .md-body blockquote { border-left: 3px solid ${token.colorBorder}; margin: 8px 0; padding-left: 12px; color: ${token.colorTextTertiary}; }
    .md-body a { color: ${token.colorPrimary}; }
  `
  return (
    <div style={style}>
      <style>{css}</style>
      <div className="md-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || ''}</ReactMarkdown>
      </div>
    </div>
  )
}
