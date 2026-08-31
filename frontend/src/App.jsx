import { useEffect, useState } from 'react'
import {
  App as AntdApp, Button, Card, Form, Input, Layout, List, Menu, Modal, Select, Space, Typography, theme,
} from 'antd'
import {
  CommentOutlined, FolderOpenOutlined, MoonOutlined, SafetyCertificateOutlined,
  SettingOutlined, SunOutlined,
} from '@ant-design/icons'
import ContractPage from './ContractPage.jsx'
import LawAssistantPage from './LawAssistantPage.jsx'
import KbManagePage from './KbManagePage.jsx'
import { PageHeader } from './ui.jsx'

const { Header, Sider, Content } = Layout
const { Text } = Typography

const NAV_ITEMS = [
  { key: 'assistant', icon: <CommentOutlined />, label: '法律助手' },
  { key: 'contract', icon: <SafetyCertificateOutlined />, label: '合同审查' },
  { key: 'kb', icon: <FolderOpenOutlined />, label: '知识库管理' },
  { key: 'settings', icon: <SettingOutlined />, label: '设置' },
]

function SettingsPage() {
  const [form] = Form.useForm()
  const [terms, setTerms] = useState([])
  const [termInput, setTermInput] = useState('')
  const [apiKeySet, setApiKeySet] = useState(false)
  const [wenshuPwdSet, setWenshuPwdSet] = useState(false)
  const { message } = AntdApp.useApp()

  useEffect(() => {
    fetch('/api/config').then((r) => r.json()).then((d) => {
      if (d.ok) {
        const llm = d.data.llm || {}
        // 密钥明文不回传：已配置则留空，占位提示；留空保存 = 保持不变
        form.setFieldsValue({
          base_url: llm.base_url || '', model: llm.model || '',
          api_key: '',
          wenshu_username: d.data.wenshu?.username || '', wenshu_password: '',
        })
        setApiKeySet(Boolean(llm.api_key_set))
        setWenshuPwdSet(Boolean(d.data.wenshu?.password_set))
      }
    })
    loadTerms()
  }, [])

  function loadTerms() {
    fetch('/api/lexicon').then((r) => r.json()).then((d) => setTerms(d.data || []))
  }

  async function save() {
    const v = await form.validateFields()
    const payload = {
      // api_key / wenshu_password 留空或未修改时后端保持原值
      llm: { base_url: v.base_url, api_key: v.api_key || '', model: v.model },
      wenshu: { username: v.wenshu_username || '', password: v.wenshu_password || '' },
    }
    const resp = await fetch('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    const d = await resp.json()
    if (d.ok) {
      message.success(`已保存，当前模型=${d.data.llm?.model}`)
      form.setFieldsValue({
        base_url: d.data.llm?.base_url || '', model: d.data.llm?.model || '',
        api_key: '',
        wenshu_username: d.data.wenshu?.username || '', wenshu_password: '',
      })
      setApiKeySet(Boolean(d.data.llm?.api_key_set))
      setWenshuPwdSet(Boolean(d.data.wenshu?.password_set))
    } else { message.error(d.error?.message || '保存失败') }
  }

  async function addTerm() {
    const t = termInput.trim()
    if (!t) return
    await fetch('/api/lexicon', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ term: t }) })
    setTermInput('')
    loadTerms()
  }

  return (
    <div>
      <PageHeader title="设置" subtitle="LLM、裁判文书网账号与自定义检索词配置" />
      <Space direction="vertical" size="large" style={{ width: '100%', maxWidth: 640 }}>
        <Form form={form} layout="vertical">
          <Card title="LLM 配置（OpenAI 兼容 API）">
            <Form.Item name="base_url" label="Base URL"><Input placeholder="https://api.deepseek.com/v1" /></Form.Item>
            <Form.Item name="api_key" label="API Key">
              <Input.Password autoComplete="new-password" placeholder={apiKeySet ? '已配置（留空保持不变）' : 'sk-...'} />
            </Form.Item>
            <Form.Item name="model" label="Model"><Input placeholder="deepseek-chat" /></Form.Item>
          </Card>
          <Card title="裁判文书网账号（类案检索）">
            <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>用于裁判文书检索 MCP 登录；密码本地 Fernet 加密存储，仅存本机。</Text>
            <Form.Item name="wenshu_username" label="账号"><Input placeholder="手机号/用户名" /></Form.Item>
            <Form.Item name="wenshu_password" label="密码">
              <Input.Password autoComplete="new-password" placeholder={wenshuPwdSet ? '已配置（留空保持不变）' : '密码'} />
            </Form.Item>
          </Card>
          <Button type="primary" onClick={save}>保存配置</Button>
        </Form>
        <Card title="自定义关键词（检索期分词增强）">
          <Space.Compact style={{ width: '100%' }}>
            <Input value={termInput} onChange={(e) => setTermInput(e.target.value)} onPressEnter={addTerm} placeholder="输入领域术语，如：实际施工人" />
            <Button onClick={addTerm}>添加</Button>
          </Space.Compact>
          <List size="small" style={{ marginTop: 12 }} dataSource={terms} renderItem={(t) => (
            <List.Item actions={[<Button size="small" onClick={async () => { await fetch('/api/lexicon/' + encodeURIComponent(t.term), { method: 'DELETE' }); loadTerms() }}>删除</Button>]}>
              <Text>{t.term}</Text>
            </List.Item>
          )} />
        </Card>
      </Space>
    </div>
  )
}

export default function App({ dark, onToggleTheme }) {
  const [active, setActive] = useState('assistant')
  const [collapsed, setCollapsed] = useState(false)
  const { token } = theme.useToken()
  const activeLabel = NAV_ITEMS.find((i) => i.key === active)?.label || ''

  return (
    <Layout style={{ height: '100vh', background: token.colorBgLayout }}>
      <Sider
        width={240}
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        collapsedWidth={64}
        theme="light"
        style={{ background: token.sidebarBg, borderRight: `1px solid ${token.colorBorder}` }}
      >
        <div style={{ height: 48, display: 'flex', alignItems: 'center', gap: 8, padding: '0 16px', borderBottom: `1px solid ${token.colorBorder}`, overflow: 'hidden' }}>
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: token.colorPrimary, flexShrink: 0 }} />
          {!collapsed && <Text strong style={{ fontSize: 15, whiteSpace: 'nowrap' }}>法律助手 Demo</Text>}
        </div>
        <Menu
          mode="inline"
          inlineCollapsed={collapsed}
          selectedKeys={[active]}
          onClick={({ key }) => setActive(key)}
          items={NAV_ITEMS}
          style={{ borderInlineEnd: 'none', paddingTop: 8, background: token.sidebarBg }}
        />
      </Sider>
      <Layout style={{ background: token.colorBgLayout }}>
        <Header style={{
          height: 56, lineHeight: '56px', padding: '0 20px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          borderBottom: `1px solid ${token.colorBorder}`,
        }}>
          <Text strong style={{ fontSize: 15 }}>{activeLabel}</Text>
          <Button size="small" type="text" icon={dark ? <SunOutlined /> : <MoonOutlined />} onClick={onToggleTheme}>
            {dark ? '浅色' : '深色'}
          </Button>
        </Header>
        <Content style={{ padding: 24, overflow: 'auto' }}>
          <div style={{ height: '100%', minWidth: 0 }}>
            {active === 'assistant' && <LawAssistantPage />}
            {active === 'contract' && <ContractPage />}
            {active === 'kb' && <KbManagePage />}
            {active === 'settings' && <SettingsPage />}
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}
