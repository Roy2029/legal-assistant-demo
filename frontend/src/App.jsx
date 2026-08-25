import { useEffect, useRef, useState } from 'react'
import { Button, Card, Collapse, Form, Input, List, Space, Tabs, Tag, Typography, message } from 'antd'
import { SendOutlined, StopOutlined } from '@ant-design/icons'

const { Text, Paragraph } = Typography

function ChatPage() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [trace, setTrace] = useState(null)
  const [citations, setCitations] = useState([])
  const abortRef = useRef(null)

  async function send() {
    const q = input.trim()
    if (!q || streaming) return
    setInput('')
    setTrace(null)
    setCitations([])
    setMessages((m) => [...m, { role: 'user', content: q }])
    setStreaming(true)
    const ctrl = new AbortController()
    abortRef.current = ctrl
    let assistant = ''
    setMessages((m) => [...m, { role: 'assistant', content: '', pending: true }])
    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, session_id: 'local-demo' }),
        signal: ctrl.signal,
      })
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n\n')
        buf = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          try {
            const evt = JSON.parse(line.slice(5).trim())
            if (evt.type === 'llm_token') {
              assistant += evt.token
              setMessages((m) => {
                const next = [...m]
                next[next.length - 1] = { role: 'assistant', content: assistant, pending: false }
                return next
              })
            } else if (evt.type === 'trace') {
              setTrace(evt.trace)
            } else if (evt.type === 'citation_check') {
              setCitations(evt)
            } else if (evt.type === 'final') {
              assistant = evt.answer
              setMessages((m) => {
                const next = [...m]
                next[next.length - 1] = { role: 'assistant', content: assistant, pending: false }
                return next
              })
            } else if (evt.type === 'error') {
              setMessages((m) => {
                const next = [...m]
                next[next.length - 1] = { role: 'assistant', content: '⚠️ ' + evt.message, pending: false }
                return next
              })
            }
          } catch {}
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') {
        setMessages((m) => {
          const next = [...m]
          next[next.length - 1] = { role: 'assistant', content: '⚠️ 请求失败: ' + e.message, pending: false }
          return next
        })
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }

  function stop() {
    if (abortRef.current) abortRef.current.abort()
  }

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 120px)', gap: 16 }}>
      <Card style={{ flex: 1, display: 'flex', flexDirection: 'column' }} styles={{ body: { flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' } }}>
        <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
          {messages.map((m, i) => (
            <div key={i} style={{ marginBottom: 12, textAlign: m.role === 'user' ? 'right' : 'left' }}>
              <Tag color={m.role === 'user' ? 'blue' : 'green'}>{m.role === 'user' ? '你' : '助手'}</Tag>
              <div style={{ display: 'inline-block', maxWidth: '80%', textAlign: 'left', whiteSpace: 'pre-wrap', background: m.role === 'user' ? '#e6f7ff' : '#f6ffed', padding: '8px 12px', borderRadius: 8 }}>
                {m.content || (m.pending ? '思考中...' : '')}
              </div>
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8, paddingTop: 8 }}>
          <Input.TextArea value={input} onChange={(e) => setInput(e.target.value)} onPressEnter={(e) => { e.preventDefault(); send() }} placeholder="输入法律问题，如：民法典第580条说了什么" autoSize={{ minRows: 2, maxRows: 6 }} disabled={streaming} />
          <Space>
            {streaming ? <Button danger icon={<StopOutlined />} onClick={stop}>停止</Button> : <Button type="primary" icon={<SendOutlined />} onClick={send}>发送</Button>}
          </Space>
        </div>
      </Card>
      <Card style={{ width: 420, overflow: 'auto' }} title="检索过程（Trace）" size="small">
        {!trace && <Text type="secondary">发送问题后展示检索过程</Text>}
        {trace && (
          <Collapse size="small" items={[
            { key: 'parsed', label: 'Query 解析', children: <pre style={{ fontSize: 12 }}>{JSON.stringify(trace.parsed, null, 2)}</pre> },
            { key: 'difficulty', label: '难度分档', children: <pre style={{ fontSize: 12 }}>{JSON.stringify(trace.difficulty, null, 2)}</pre> },
            { key: 'retrieval', label: `检索（rrf ${trace.rrf_raw_count} → rerank 后 ${trace.final_count}）`, children: <Text type="secondary">详细 chunk 分数将在 W3 后续版本展示</Text> },
          ]} defaultActiveKey={['parsed']} />
        )}
        {citations && citations.unverifiable && (
          <Paragraph style={{ marginTop: 12 }} type="warning">
            ⚠️ 未能验证的引用：{citations.unverifiable.join('、')}
          </Paragraph>
        )}
      </Card>
    </div>
  )
}

function SettingsPage() {
  const [form] = Form.useForm()
  const [terms, setTerms] = useState([])
  const [termInput, setTermInput] = useState('')

  useEffect(() => {
    fetch('/api/config').then((r) => r.json()).then((d) => {
      if (d.ok) form.setFieldsValue({ base_url: d.data.llm?.base_url, api_key: d.data.llm?.api_key, model: d.data.llm?.model })
    })
    loadTerms()
  }, [])

  function loadTerms() {
    fetch('/api/lexicon').then((r) => r.json()).then((d) => setTerms(d.data || []))
  }

  async function save() {
    const v = await form.validateFields()
    const resp = await fetch('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ llm: v }) })
    const d = await resp.json()
    if (d.ok) message.success('已保存（热更新生效）')
  }

  async function addTerm() {
    const t = termInput.trim()
    if (!t) return
    await fetch('/api/lexicon', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ term: t }) })
    setTermInput('')
    loadTerms()
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Card title="LLM 配置（OpenAI 兼容 API）" style={{ maxWidth: 560 }}>
        <Form form={form} layout="vertical">
          <Form.Item name="base_url" label="Base URL" rules={[{ required: true }]}><Input placeholder="https://api.deepseek.com/v1" /></Form.Item>
          <Form.Item name="api_key" label="API Key" rules={[{ required: true }]}><Input.Password placeholder="sk-..." /></Form.Item>
          <Form.Item name="model" label="Model" rules={[{ required: true }]}><Input placeholder="deepseek-chat" /></Form.Item>
          <Button type="primary" onClick={save}>保存配置</Button>
        </Form>
      </Card>
      <Card title="自定义关键词（检索期分词增强）" style={{ maxWidth: 560 }}>
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
  )
}

function Placeholder({ title }) {
  return <Card><Text type="secondary">{title}（开发中）</Text></Card>
}

export default function App() {
  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: 16 }}>
      <Typography.Title level={3} style={{ marginBottom: 16 }}>法律助手 Demo</Typography.Title>
      <Tabs defaultActiveKey="chat" items={[
        { key: 'chat', label: '知识库问答', children: <ChatPage /> },
        { key: 'assistant', label: '实务助手', children: <Placeholder title="实务助手" /> },
        { key: 'kb', label: '知识库管理', children: <Placeholder title="知识库管理" /> },
        { key: 'settings', label: '设置', children: <SettingsPage /> },
      ]} />
    </div>
  )
}
