import { useEffect, useRef, useState } from 'react'
import { Button, Card, Collapse, Form, Input, List, Modal, Space, Tabs, Tag, Typography, message } from 'antd'
import { SendOutlined, StopOutlined } from '@ant-design/icons'

const { Text, Paragraph } = Typography

function ChunkList({ items }) {
  return (
    <Collapse size="small" items={(items || []).map((c, i) => ({
      key: String(i),
      label: <span style={{ fontSize: 12 }}>#{i + 1} score={c.score} {c.chunk_id?.slice(0, 18)}</span>,
      children: <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto' }}>{c.text}</pre>,
    }))} />
  )
}

function ChatPage() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [trace, setTrace] = useState(null)
  const [citations, setCitations] = useState([])
  const [chunkModal, setChunkModal] = useState(null)
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
              if (evt.citations) setCitations(evt.citations)
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
        {citations.length > 0 && (
          <div style={{ padding: '4px 8px' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>引用法条（点击定位原文）：</Text>{' '}
            {citations.map((c, i) => (
              <Tag key={i} color="blue" style={{ cursor: 'pointer' }} onClick={async () => {
                const r = await fetch('/api/chunk/locate?law_name=' + encodeURIComponent(c.law_name) + '&article_no=' + encodeURIComponent(c.article_no))
                const d = await r.json()
                setChunkModal(d.ok ? d.data : { text: '未定位到原文', law_name: c.law_name, article_no: c.article_no })
              }}>{c.law_name} 第{c.article_no}条</Tag>
            ))}
          </div>
        )}
        <Modal open={!!chunkModal} onCancel={() => setChunkModal(null)} footer={null} title={chunkModal ? `${chunkModal.law_name} 第${chunkModal.article_no}条` : ''}>
          <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 400, overflow: 'auto', fontSize: 13 }}>{chunkModal?.text}</pre>
        </Modal>
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
          <Collapse size="small" defaultActiveKey={['parsed']} items={[
            { key: 'parsed', label: 'Query 解析', children: <pre style={{ fontSize: 12 }}>{JSON.stringify(trace.parsed, null, 2)}</pre> },
            { key: 'difficulty', label: '难度分档', children: <pre style={{ fontSize: 12 }}>{JSON.stringify(trace.difficulty, null, 2)}</pre> },
            { key: 'bm25_tokens', label: 'BM25 查询分词', children: (
                <div>
                  {(trace.bm25_tokens || []).map((t, i) => <Tag key={i} style={{ marginBottom: 4 }}>{t}</Tag>)}
                  <Text type="secondary" style={{ display: 'block', marginTop: 4, fontSize: 12 }}>自定义关键词生效：分词中包含完整词条即生效</Text>
                </div>
              ) },
            {
              key: 'dense', label: `Dense 召回（${(trace.dense_topk || []).length}）`,
              children: <ChunkList items={trace.dense_topk || []} />,
            },
            {
              key: 'bm25', label: `BM25 召回（${(trace.bm25_topk || []).length}）`,
              children: <ChunkList items={trace.bm25_topk || []} />,
            },
          ]} />
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

function AssistantPage() {
  const [actions, setActions] = useState([])
  const [action, setAction] = useState(null)
  const [input, setInput] = useState('')
  const [steps, setSteps] = useState([])
  const [finalText, setFinalText] = useState('')
  const [running, setRunning] = useState(false)

  useEffect(() => {
    fetch('/api/actions').then((r) => r.json()).then((d) => setActions(d.data || []))
  }, [])

  async function run() {
    if (!action || !input.trim() || running) return
    setSteps([]); setFinalText(''); setRunning(true)
    const resp = await fetch('/api/assistant', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action.skill_id, query: input.trim() }),
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
          if (evt.type === 'step_start') setSteps((s) => [...s, { step: evt.step, status: 'running' }])
          else if (evt.type === 'tool_call') setSteps((s) => [...s, { tool: evt.tool, params: evt.params, status: 'tool' }])
          else if (evt.type === 'tool_result') setSteps((s) => [...s, { summary: evt.summary, status: 'done' }])
          else if (evt.type === 'step_end') setSteps((s) => [...s, { step: evt.step + ' 完成', summary: evt.summary, status: 'done' }])
          else if (evt.type === 'final') setFinalText(evt.answer)
          else if (evt.type === 'error') setFinalText('⚠️ ' + evt.message)
        } catch {}
      }
    }
    setRunning(false)
  }

  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <Card style={{ width: 360 }} title="业务动作" size="small">
        <Space direction="vertical" style={{ width: '100%' }}>
          {actions.map((a) => (
            <Card key={a.skill_id} size="small" hoverable onClick={() => setAction(a)}
              style={{ border: action?.skill_id === a.skill_id ? '1px solid #1677ff' : undefined }}>
              <Text strong>{a.name}</Text>
              <div><Text type="secondary" style={{ fontSize: 12 }}>{a.description}</Text></div>
            </Card>
          ))}
        </Space>
      </Card>
      <Card style={{ flex: 1 }} title="执行" size="small">
        {!action && <Text type="secondary">请先选择左侧业务动作</Text>}
        {action && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Input.TextArea value={input} onChange={(e) => setInput(e.target.value)} placeholder="描述你的需求" autoSize={{ minRows: 2, maxRows: 5 }} />
            <Button type="primary" onClick={run} loading={running}>{running ? '执行中...' : '执行'}</Button>
            {steps.length > 0 && (
              <List size="small" dataSource={steps} renderItem={(s, i) => (
                <List.Item>
                  <Tag color={s.status === 'done' ? 'green' : 'blue'}>{(s.step || s.tool || '') + (s.summary ? '：' + s.summary : '')}</Tag>
                </List.Item>
              )} />
            )}
            {finalText && <Paragraph style={{ whiteSpace: 'pre-wrap', background: '#f6ffed', padding: 12, borderRadius: 8 }}>{finalText}</Paragraph>}
          </Space>
        )}
      </Card>
    </div>
  )
}

function KbPage() {
  const [docs, setDocs] = useState([])
  const [uploading, setUploading] = useState(false)

  function loadDocs() {
    fetch('/api/kb/docs').then((r) => r.json()).then((d) => setDocs(d.data || []))
  }
  useEffect(() => { loadDocs() }, [])

  async function upload(file) {
    setUploading(true)
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/kb/upload', { method: 'POST', body: fd })
    const d = await r.json()
    if (d.ok) { message.success(`已上传：${d.data.name}（${d.data.children} chunks）`) } else { message.error(d.error?.message || '上传失败') }
    setUploading(false)
    loadDocs()
  }

  async function remove(docId) {
    await fetch('/api/kb/docs/' + docId, { method: 'DELETE' })
    message.success('已删除')
    loadDocs()
  }

  return (
    <Card title="知识库管理" size="small">
      <Space direction="vertical" style={{ width: '100%' }}>
        <input type="file" accept=".md,.txt,.docx,.pdf" onChange={(e) => { if (e.target.files[0]) upload(e.target.files[0]) }} disabled={uploading} />
        <Text type="secondary">支持 md / txt / docx / pdf（扫描件暂不支持）</Text>
        <List size="small" dataSource={docs} renderItem={(d) => (
          <List.Item actions={[<Button size="small" danger onClick={() => remove(d.doc_id)}>删除</Button>]}>
            <Text>{d.file_path?.replace(/.*[\/]/, '')}</Text>
            <Tag>{d.parse_status}</Tag>
            <Text type="secondary">{d.chunk_count} chunks</Text>
          </List.Item>
        )} />
      </Space>
    </Card>
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
        { key: 'assistant', label: '实务助手', children: <AssistantPage /> },
        { key: 'kb', label: '知识库管理', children: <KbPage /> },
        { key: 'settings', label: '设置', children: <SettingsPage /> },
      ]} />
    </div>
  )
}
