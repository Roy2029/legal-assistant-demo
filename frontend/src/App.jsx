import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button, Card, Checkbox, Collapse, Form, Input, List, Modal, Select, Space, Tabs, Tag, Typography, message } from 'antd'
import { SendOutlined, StopOutlined } from '@ant-design/icons'

const { Text, Paragraph } = Typography

function Markdown({ content, style }) {
  return (
    <div style={style} className="md-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || ''}</ReactMarkdown>
    </div>
  )
}

const FEEDBACK_REASONS = [
  { value: 'retrieval', label: '检索错（找不到/找错法条）' },
  { value: 'citation', label: '引用错（编造/张冠李戴）' },
  { value: 'off_topic', label: '答非所问' },
  { value: 'format', label: '格式差/不完整' },
  { value: 'other', label: '其他' },
]

function Feedback({ mode, action, sessionId, traceId, query, answer, trace }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('retrieval')
  const [note, setNote] = useState('')
  const [sending, setSending] = useState(false)
  const [done, setDone] = useState(false)

  async function submit(r, n) {
    if (sending) return
    setSending(true)
    try {
      const resp = await fetch('/api/badcases', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, action, session_id: sessionId, trace_id: traceId, query, answer, reason: r, note: n, trace }),
      })
      const d = await resp.json()
      if (d.ok) { setDone(true); setOpen(false); message.success('已记录，感谢反馈') } else { message.error(d.error?.message || '提交失败') }
    } catch (e) { message.error('提交失败: ' + e.message) } finally { setSending(false) }
  }

  if (done) return <Text type="secondary" style={{ fontSize: 12 }}>已收到反馈，感谢。</Text>
  return (
    <>
      <Space size={8} style={{ marginTop: 4 }}>
        <Button size="small" onClick={() => submit('good', '')}>有帮助</Button>
        <Button size="small" danger onClick={() => setOpen(true)}>没帮助</Button>
      </Space>
      <Modal open={open} title="哪里出了问题？" onCancel={() => setOpen(false)} onOk={() => submit(reason, note)} okText="提交" cancelText="取消" confirmLoading={sending}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text type="secondary">选择最接近的一类原因：</Text>
          <Select value={reason} onChange={setReason} options={FEEDBACK_REASONS} style={{ width: '100%' }} />
          <Text type="secondary">补充说明（可选）：</Text>
          <Input.TextArea value={note} onChange={(e) => setNote(e.target.value)} placeholder="例如：检索结果里没有劳动合同法第19条" autoSize={{ minRows: 2, maxRows: 5 }} />
        </Space>
      </Modal>
    </>
  )
}

function displayFileName(filePath) {
  const name = (filePath || '').split(/[\\/]/).pop() || ''
  return name.replace(/^[0-9a-f]{32}__/, '')
}

function ChunkList({ items, onOpenFull }) {
  if (!items || items.length === 0) return <Text type="secondary" style={{ fontSize: 12 }}>（无结果）</Text>
  return (
    <div>
      {items.map((c, i) => (
        <Card key={i} size="small" style={{ marginBottom: 6 }} styles={{ body: { padding: 8 } }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 4, flexWrap: 'wrap' }}>
            <Space size={4} wrap style={{ flex: 1, minWidth: 0 }}>
              <Text strong style={{ fontSize: 12 }}>#{i + 1}</Text>
              <Tag color="blue" style={{ whiteSpace: 'normal', wordBreak: 'break-all' }}>{c.meta?.law_name || '未知'}{c.meta?.article_no ? ' 第' + c.meta.article_no + '条' : ''}</Tag>
              {c.meta?.heading_path?.length > 0 && c.meta.heading_path[c.meta.heading_path.length - 1] !== c.meta.law_name && (
                <Tag color="cyan" style={{ whiteSpace: 'normal', wordBreak: 'break-all' }}>{c.meta.heading_path[c.meta.heading_path.length - 1]}</Tag>
              )}
              <Tag>{c.meta?.chunk_level || '-'}</Tag>
              {c.meta?.corpus && <Tag color={c.meta.corpus === 'user' ? 'orange' : 'green'}>{c.meta.corpus}</Tag>}
              <Text type="secondary" style={{ fontSize: 11 }}>score={c.score}</Text>
            </Space>
            <Button size="small" type="link" style={{ flexShrink: 0 }} onClick={() => onOpenFull && onOpenFull(c)}>查看原文</Button>
          </div>
          <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 80, overflow: 'hidden', marginTop: 4 }}>{c.text}</pre>
        </Card>
      ))}
    </div>
  )
}

function ChatPage() {
  const [messages, setMessages] = useState([])
  const [sessions, setSessions] = useState([])
  const [sessionId, setSessionId] = useState('local-demo')

  useEffect(() => { loadSessions(); loadFolders() }, [])

  async function loadFolders() {
    const r = await fetch('/api/kb/folders')
    const d = await r.json()
    if (d.ok) setFolderOptions((d.data || []).map((f) => ({ value: f.kb_id, label: f.name })))
  }

  async function loadSessions() {
    const r = await fetch('/api/sessions')
    const d = await r.json()
    if (d.ok && d.data.length) { setSessions(d.data); setSessionId(d.data[0].session_id); loadMessages(d.data[0].session_id) }
  }

  async function loadMessages(sid) {
    const r = await fetch('/api/sessions/' + sid + '/messages')
    const d = await r.json()
    if (d.ok) setMessages(d.data.map((m) => ({ role: m.role, content: m.content })))
  }

  async function newSession() {
    const r = await fetch('/api/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: '新会话' }) })
    const d = await r.json()
    if (d.ok) { setSessionId(d.data.session_id); setMessages([]); loadSessions() }
  }

  async function deleteSession() {
    await fetch('/api/sessions/' + sessionId, { method: 'DELETE' })
    setMessages([])
    loadSessions()
  }

  async function renameSession() {
    const title = renameText.trim()
    if (!title) return
    const r = await fetch('/api/sessions/' + sessionId, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) })
    const d = await r.json()
    if (d.ok) { message.success('已重命名'); setRenameOpen(false); loadSessions() } else { message.error(d.error?.message || '重命名失败') }
  }
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [trace, setTrace] = useState(null)
  const [citations, setCitations] = useState([])
  const [chunkModal, setChunkModal] = useState(null)
  const [fullChunk, setFullChunk] = useState(null)
  const [lastQuery, setLastQuery] = useState('')
  const [lastAnswer, setLastAnswer] = useState('')
  const [traceId, setTraceId] = useState(null)
  const [folderOptions, setFolderOptions] = useState([])
  const [folderFilter, setFolderFilter] = useState([])
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameText, setRenameText] = useState('')

  async function openFullChunk(c) {
    try {
      const r = await fetch('/api/chunk/' + encodeURIComponent(c.chunk_id))
      const d = await r.json()
      if (d.ok) { setFullChunk(d.data); return }
      setFullChunk({
        text: (c.text || '') + '\n\n[全文加载失败：' + (d.error?.message || '未知错误') + '，以下为截断预览]',
        law_name: c.meta?.law_name, article_no: c.meta?.article_no, chunk_id: c.chunk_id,
      })
    } catch (e) {
      setFullChunk({
        text: (c.text || '') + '\n\n[全文加载失败：' + e.message + '，以下为截断预览]',
        law_name: c.meta?.law_name, article_no: c.meta?.article_no, chunk_id: c.chunk_id,
      })
    }
  }
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
        body: JSON.stringify({ query: q, session_id: sessionId, folders: folderFilter.length ? folderFilter : undefined }),
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
            if (evt.type === 'session_start') {
              setTraceId(evt.trace_id)
            } else if (evt.type === 'llm_token') {
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
              setLastQuery(q)
              setLastAnswer(evt.answer)
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
              <div style={{ display: 'inline-block', maxWidth: '80%', textAlign: 'left', background: m.role === 'user' ? '#e6f7ff' : '#f6ffed', padding: '8px 12px', borderRadius: 8, verticalAlign: 'top' }}>
                {m.role === 'assistant'
                  ? <Markdown content={m.content || (m.pending ? '思考中...' : '')} style={{ whiteSpace: 'normal' }} />
                  : <div style={{ whiteSpace: 'pre-wrap' }}>{m.content || ''}</div>}
              </div>
            </div>
          ))}
          {lastAnswer && !streaming && (
            <div style={{ padding: '0 8px 4px' }}>
              <Feedback mode="chat" sessionId={sessionId} traceId={traceId} query={lastQuery} answer={lastAnswer} trace={trace} />
            </div>
          )}
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
        <Modal open={!!fullChunk} onCancel={() => setFullChunk(null)} footer={null} title={fullChunk ? `${fullChunk.law_name || ''} ${fullChunk.article_no ? '第' + fullChunk.article_no + '条' : ''} ${fullChunk.chunk_id || ''}`.trim() : ''} width={720}>
          <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 480, overflow: 'auto', fontSize: 13 }}>{fullChunk?.text}</pre>
        </Modal>
        <Modal open={renameOpen} onCancel={() => setRenameOpen(false)} onOk={renameSession} okText="保存" cancelText="取消" title="会话重命名">
          <Input value={renameText} onChange={(e) => setRenameText(e.target.value)} placeholder="会话标题" />
        </Modal>
        <Modal open={!!chunkModal} onCancel={() => setChunkModal(null)} footer={null} title={chunkModal ? `${chunkModal.law_name} 第${chunkModal.article_no}条` : ''}>
          <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 400, overflow: 'auto', fontSize: 13 }}>{chunkModal?.text}</pre>
        </Modal>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
          <Select size='small' style={{ width: 220 }} value={sessionId} onChange={(v) => { setSessionId(v); loadMessages(v) }} options={sessions.map((s) => ({ value: s.session_id, label: s.title || s.session_id.slice(0, 8) }))} />
          <Button size='small' onClick={newSession}>新建会话</Button>
          <Button size='small' onClick={() => { setRenameText(sessions.find((s) => s.session_id === sessionId)?.title || ''); setRenameOpen(true) }}>重命名</Button>
          <Button size='small' danger onClick={deleteSession}>删除</Button>
          <Select size='small' mode="multiple" allowClear style={{ minWidth: 260, flex: 1 }} placeholder="全部知识库（公共+本人）" value={folderFilter} onChange={setFolderFilter}
            options={[{ value: '__public__', label: '公共法律库' }, ...folderOptions]} />
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
          <Collapse size="small" defaultActiveKey={['final']} items={[
            {
              key: 'final', label: `最终上下文（RRF top-${trace.final_count}，难度：${trace.difficulty?.level || '-'}）`,
              children: <ChunkList items={trace.final_topk || []} onOpenFull={openFullChunk} />,
            },
            {
              key: 'rrf_raw', label: `RRF 原始 top-10（未按难度截断）`,
              children: <ChunkList items={trace.rrf_raw_topk || []} onOpenFull={openFullChunk} />,
            },
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
              children: <ChunkList items={trace.dense_topk || []} onOpenFull={openFullChunk} />,
            },
            {
              key: 'bm25', label: `BM25 召回（${(trace.bm25_topk || []).length}）`,
              children: <ChunkList items={trace.bm25_topk || []} onOpenFull={openFullChunk} />,
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
      if (d.ok) form.setFieldsValue({
        base_url: d.data.llm?.base_url, api_key: d.data.llm?.api_key, model: d.data.llm?.model,
        wenshu_username: d.data.wenshu?.username || '', wenshu_password: d.data.wenshu?.password || '',
      })
    })
    loadTerms()
  }, [])

  function loadTerms() {
    fetch('/api/lexicon').then((r) => r.json()).then((d) => setTerms(d.data || []))
  }

  async function save() {
    const v = await form.validateFields()
    const payload = {
      llm: { base_url: v.base_url, api_key: v.api_key, model: v.model },
      wenshu: { username: v.wenshu_username || '', password: v.wenshu_password || '' },
    }
    const resp = await fetch('/api/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    const d = await resp.json()
    if (d.ok) {
      message.success(`已保存，当前模型=${d.data.llm?.model}`)
      form.setFieldsValue({
        base_url: d.data.llm?.base_url, api_key: d.data.llm?.api_key, model: d.data.llm?.model,
        wenshu_username: d.data.wenshu?.username || '', wenshu_password: d.data.wenshu?.password || '',
      })
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
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Form form={form} layout="vertical">
        <Card title="LLM 配置（OpenAI 兼容 API）" style={{ maxWidth: 560 }}>
          <Form.Item name="base_url" label="Base URL" rules={[{ required: true }]}><Input placeholder="https://api.deepseek.com/v1" /></Form.Item>
          <Form.Item name="api_key" label="API Key" rules={[{ required: true }]}><Input.Password placeholder="sk-..." /></Form.Item>
          <Form.Item name="model" label="Model" rules={[{ required: true }]}><Input placeholder="deepseek-chat" /></Form.Item>
        </Card>
        <Card title="裁判文书网账号（类案检索）" style={{ maxWidth: 560, marginTop: 16 }}>
          <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>用于裁判文书检索 MCP 登录；密码本地 Fernet 加密存储，仅存本机。</Text>
          <Form.Item name="wenshu_username" label="账号"><Input placeholder="手机号/用户名" /></Form.Item>
          <Form.Item name="wenshu_password" label="密码"><Input.Password placeholder="密码" /></Form.Item>
        </Card>
        <Button type="primary" onClick={save} style={{ marginTop: 8 }}>保存配置</Button>
      </Form>
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
  const [mode, setMode] = useState('rag')
  const [input, setInput] = useState('')
  const [steps, setSteps] = useState([])
  const [finalText, setFinalText] = useState('')
  const [finalReport, setFinalReport] = useState('')
  const [running, setRunning] = useState(false)
  const [traceId, setTraceId] = useState(null)
  const [sessions, setSessions] = useState([])
  const [sessionId, setSessionId] = useState(null)
  const [history, setHistory] = useState([])

  useEffect(() => { loadSessions() }, [])

  async function loadSessions() {
    const r = await fetch('/api/sessions?mode=assistant')
    const d = await r.json()
    if (d.ok) {
      const ss = d.data || []
      setSessions(ss)
      if (ss.length) { setSessionId(ss[0].session_id); loadMessages(ss[0].session_id) } else { setSessionId(null); setHistory([]) }
    }
  }

  async function loadMessages(sid) {
    const r = await fetch('/api/sessions/' + sid + '/messages')
    const d = await r.json()
    if (d.ok) setHistory(d.data.map((m) => ({ role: m.role, content: m.content })))
  }

  async function newSession() {
    const r = await fetch('/api/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: '新会话', mode: 'assistant' }) })
    const d = await r.json()
    if (d.ok) { setSessionId(d.data.session_id); setHistory([]); loadSessions() }
  }

  async function deleteSession() {
    if (!sessionId) return
    await fetch('/api/sessions/' + sessionId, { method: 'DELETE' })
    setSessionId(null); setHistory([]); loadSessions()
  }

  const modes = [
    { key: 'case_analysis', name: '案件分析', desc: '主 agent：按 skill 步骤调度工具', status: '可用' },
    { key: 'rag', name: '知识库检索', desc: 'RAG agent：ReAct 多跳检索法律依据', status: '可用' },
    { key: 'case', name: '类案检索', desc: 'Case agent：裁判文书 MCP 类案检索', status: '可用' },
    { key: 'contract_review', name: '合同审查', desc: '合同审查 agent（待开发）', status: '待开发' },
  ]

  async function run() {
    const q = input.trim()
    if (!q || running) return
    if (mode === 'contract_review') { message.info('合同审查 agent 待开发，敬请期待'); return }
    setSteps([]); setFinalText(''); setFinalReport(''); setRunning(true)
    const url = mode === 'case_analysis' ? '/api/assistant' : (mode === 'rag' ? '/api/rag-agent' : '/api/case-agent')
    const body = mode === 'case_analysis' ? { action: 'case_analysis', query: q, session_id: sessionId } : { query: q, session_id: sessionId }
    const resp = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
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
          if (evt.type === 'session_start') setTraceId(evt.trace_id)
          else if (evt.type === 'step_start') setSteps((s) => [...s, { step: evt.step, status: 'running' }])
          else if (evt.type === 'tool_call') setSteps((s) => [...s, { tool: evt.tool, params: evt.params, status: 'tool' }])
          else if (evt.type === 'tool_result') setSteps((s) => [...s, { summary: evt.summary, status: 'done' }])
          else if (evt.type === 'agent_start') setSteps((s) => [...s, { step: (evt.agent === 'knowledge' ? '知识库检索 agent' : '类案检索 agent') + ' 启动', status: 'running' }])
          else if (evt.type === 'agent_think') setSteps((s) => [...s, { step: '推理', summary: (evt.text || '').slice(0, 120), status: 'done' }])
          else if (evt.type === 'agent_tool_call') setSteps((s) => [...s, { tool: evt.tool, params: evt.params, status: 'tool' }])
          else if (evt.type === 'agent_tool_result') setSteps((s) => [...s, { summary: (evt.summary || '').slice(0, 160), status: 'done' }])
          else if (evt.type === 'agent_retry') setSteps((s) => [...s, { step: '重试', summary: evt.reason, status: 'running' }])
          else if (evt.type === 'agent_report') setSteps((s) => [...s, { step: '检索报告', summary: evt.answer || (evt.needs_human ? '需人工介入' : '已生成'), status: 'done' }])
          else if (evt.type === 'step_end') setSteps((s) => [...s, { step: evt.step + ' 完成', summary: evt.summary, status: 'done' }])
          else if (evt.type === 'final') { setFinalText(evt.answer || ''); if (evt.report) setFinalReport(evt.report) }
          else if (evt.type === 'error') setFinalText('⚠️ ' + evt.message)
        } catch {}
      }
    }
    setRunning(false)
    if (sessionId) { loadMessages(sessionId); loadSessions() }
  }

  return (
    <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
      <Card style={{ width: 320, flexShrink: 0 }} title="Tool Agent" size="small">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space wrap>
            <Select size="small" style={{ width: 160 }} value={sessionId} onChange={(v) => { setSessionId(v); loadMessages(v) }}
              options={sessions.map((s) => ({ value: s.session_id, label: s.title || s.session_id.slice(0, 8) }))} />
            <Button size="small" onClick={newSession}>新建会话</Button>
            <Button size="small" danger onClick={deleteSession} disabled={!sessionId}>删除</Button>
          </Space>
          {modes.map((m) => (
            <Card key={m.key} size="small" hoverable onClick={() => setMode(m.key)}
              style={{ border: mode === m.key ? '1px solid #1677ff' : undefined }}>
              <Space direction="vertical" size={0} style={{ width: '100%' }}>
                <Space><Text strong>{m.name}</Text>{m.status === '待开发' ? <Tag color="orange">待开发</Tag> : <Tag color="green">可用</Tag>}</Space>
                <Text type="secondary" style={{ fontSize: 12 }}>{m.desc}</Text>
              </Space>
            </Card>
          ))}
        </Space>
      </Card>
      <Card style={{ flex: 1, minWidth: 0 }} title="执行" size="small">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input.TextArea value={input} onChange={(e) => setInput(e.target.value)} placeholder="输入法律问题或案情要点" autoSize={{ minRows: 2, maxRows: 5 }} disabled={running} />
          <Space>
            <Button type="primary" onClick={run} loading={running}>{running ? '执行中...' : '执行'}</Button>
            {mode === 'case' && <Text type="secondary" style={{ fontSize: 12 }}>类案检索需要先在设置页填写裁判文书网账号</Text>}
          </Space>
          {history.length > 0 && !steps.length && (
            <div style={{ maxHeight: 300, overflow: 'auto' }}>
              {history.map((m, i) => (
                <div key={i} style={{ marginBottom: 8, textAlign: m.role === 'user' ? 'right' : 'left' }}>
                  <Tag color={m.role === 'user' ? 'blue' : 'green'}>{m.role === 'user' ? '你' : '助手'}</Tag>
                  <div style={{ display: 'inline-block', maxWidth: '85%', textAlign: 'left', background: m.role === 'user' ? '#e6f7ff' : '#f6ffed', padding: '6px 10px', borderRadius: 8, whiteSpace: 'pre-wrap' }}>{m.content}</div>
                </div>
              ))}
            </div>
          )}
          {steps.length > 0 && (
            <List size="small" dataSource={steps} renderItem={(s, i) => (
              <List.Item key={i}>
                <Tag color={s.status === 'done' ? 'green' : 'blue'}>{(s.step || s.tool || '') + (s.summary ? '：' + s.summary : '')}</Tag>
              </List.Item>
            )} />
          )}
          {finalText && <Markdown content={finalText} style={{ background: '#f6ffed', padding: 12, borderRadius: 8 }} />}
          {finalReport && <Markdown content={finalReport} style={{ background: '#fffbe6', padding: 12, borderRadius: 8 }} />}
        </Space>
      </Card>
    </div>
  )
}

function KbPage() {
  const [docs, setDocs] = useState([])
  const [folders, setFolders] = useState([])
  const [folder, setFolder] = useState('default')
  const [filterFolder, setFilterFolder] = useState(undefined)
  const [newFolder, setNewFolder] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState('')
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildMsg, setRebuildMsg] = useState('')
  const [chunkViewDoc, setChunkViewDoc] = useState(null)
  const [chunks, setChunks] = useState([])
  const [editingChunk, setEditingChunk] = useState(null)
  const [editText, setEditText] = useState('')
  const [splittingChunk, setSplittingChunk] = useState(null)
  const [splitPart1, setSplitPart1] = useState('')
  const [splitPart2, setSplitPart2] = useState('')
  const [chunkBusy, setChunkBusy] = useState(false)
  const [selectedIds, setSelectedIds] = useState([])
  const [batchMoveFolder, setBatchMoveFolder] = useState(undefined)
  const [renamingFolder, setRenamingFolder] = useState(null)
  const [renameText, setRenameText] = useState('')

  async function viewChunks(docId) {
    const r = await fetch('/api/kb/docs/' + encodeURIComponent(docId) + '/chunks')
    const d = await r.json()
    if (d.ok) { setChunks(d.data || []); setChunkViewDoc(docId) } else { message.error(d.error?.message || '获取分块失败') }
  }

  async function saveChunkEdit() {
    if (!editingChunk || !editText.trim() || chunkBusy) return
    setChunkBusy(true)
    try {
      const r = await fetch(`/api/kb/docs/${encodeURIComponent(chunkViewDoc)}/chunks/${encodeURIComponent(editingChunk.chunk_id)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: editText }),
      })
      const d = await r.json()
      if (d.ok) { message.success('已更新分块'); setEditingChunk(null); viewChunks(chunkViewDoc); loadDocs() } else { message.error(d.error?.message || '更新失败') }
    } catch (e) { message.error('更新失败: ' + e.message) } finally { setChunkBusy(false) }
  }

  function openSplit(c) {
    const text = c.text || ''
    const NL = String.fromCharCode(10)
    const puncts = ['。', '；', ';', '！', '？']
    const mid = Math.floor(text.length / 2)
    let cut = mid
    for (let i = 0; i < 120; i++) {
      const pos = mid + i
      if (pos < text.length && (puncts.includes(text[pos]) || text[pos] === NL)) { cut = pos + 1; break }
      const neg = mid - i
      if (neg > 0 && (puncts.includes(text[neg]) || text[neg] === NL)) { cut = neg + 1; break }
    }
    setSplitPart1(text.slice(0, cut).trim())
    setSplitPart2(text.slice(cut).trim())
    setSplittingChunk(c)
  }

  async function saveChunkSplit() {
    if (!splittingChunk || !splitPart1.trim() || !splitPart2.trim() || chunkBusy) return
    setChunkBusy(true)
    try {
      const r = await fetch(`/api/kb/docs/${encodeURIComponent(chunkViewDoc)}/chunks/${encodeURIComponent(splittingChunk.chunk_id)}/split`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ part1: splitPart1, part2: splitPart2 }),
      })
      const d = await r.json()
      if (d.ok) { message.success('已拆分分块'); setSplittingChunk(null); viewChunks(chunkViewDoc); loadDocs() } else { message.error(d.error?.message || '拆分失败') }
    } catch (e) { message.error('拆分失败: ' + e.message) } finally { setChunkBusy(false) }
  }

  async function mergeChunkWithPrev(i) {
    if (i <= 0 || chunkBusy) return
    const c1 = chunks[i - 1]
    const c2 = chunks[i]
    Modal.confirm({
      title: '合并分块',
      content: `将 #${i} 与 #${i + 1} 合并为一段？`,
      okText: '合并', cancelText: '取消',
      onOk: async () => {
        setChunkBusy(true)
        try {
          const r = await fetch(`/api/kb/docs/${encodeURIComponent(chunkViewDoc)}/chunks/merge`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chunk_id1: c1.chunk_id, chunk_id2: c2.chunk_id }),
          })
          const d = await r.json()
          if (d.ok) { message.success('已合并分块'); viewChunks(chunkViewDoc); loadDocs() } else { message.error(d.error?.message || '合并失败') }
        } catch (e) { message.error('合并失败: ' + e.message) } finally { setChunkBusy(false) }
      },
    })
  }

  async function deleteChunk(c) {
    if (chunkBusy) return
    Modal.confirm({
      title: '删除分块',
      content: '删除该分块后不可恢复，确认删除？',
      okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        setChunkBusy(true)
        try {
          const r = await fetch(`/api/kb/docs/${encodeURIComponent(chunkViewDoc)}/chunks/${encodeURIComponent(c.chunk_id)}`, { method: 'DELETE' })
          const d = await r.json()
          if (d.ok) { message.success('已删除分块'); viewChunks(chunkViewDoc); loadDocs() } else { message.error(d.error?.message || '删除失败') }
        } catch (e) { message.error('删除失败: ' + e.message) } finally { setChunkBusy(false) }
      },
    })
  }

  function loadDocs() {
    const url = filterFolder ? '/api/kb/docs?folder=' + encodeURIComponent(filterFolder) : '/api/kb/docs'
    fetch(url).then((r) => r.json()).then((d) => setDocs(d.data || []))
  }
  function loadFolders() {
    fetch('/api/kb/folders').then((r) => r.json()).then((d) => {
      const fs = d.data || []
      setFolders(fs)
      if (!fs.find((f) => f.kb_id === folder)) setFolder('default')
    })
  }
  useEffect(() => { loadDocs(); loadFolders() }, [])
  useEffect(() => { loadDocs() }, [filterFolder])

  async function createFolder() {
    const name = newFolder.trim()
    if (!name) return
    const r = await fetch('/api/kb/folders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
    const d = await r.json()
    if (d.ok) { message.success(`已创建文件夹：${name}`); setNewFolder(''); setFolder(name); loadFolders() } else { message.error(d.error?.message || '创建失败') }
  }

  async function rebuildKb() {
    Modal.confirm({
      title: '重建法律库索引',
      content: '将释放检索服务并后台重建索引，期间知识库问答可能暂时不可用。确认继续？',
      okText: '重建', cancelText: '取消',
      onOk: async () => {
        setRebuilding(true); setRebuildMsg('重建启动中...')
        const r = await fetch('/api/kb/rebuild', { method: 'POST' })
        const d = await r.json()
        if (!d.ok) { message.error(d.error?.message || '启动失败'); setRebuilding(false); return }
        const timer = setInterval(async () => {
          try {
            const s = await fetch('/api/kb/rebuild/status').then((x) => x.json())
            const st = s.data || {}
            if (st.running) { setRebuildMsg(`重建中...（已启动 ${st.started_at || ''}）`) }
            else { clearInterval(timer); setRebuilding(false); setRebuildMsg(st.ok ? '重建完成' : `重建失败：${st.error || '未知错误'}`); message.info('法律库索引已更新'); loadFolders(); loadDocs() }
          } catch (e) { clearInterval(timer); setRebuilding(false); setRebuildMsg('状态查询失败') }
        }, 5000)
      },
    })
  }

  async function saveRenameFolder() {
    const name = (renameText || '').trim()
    if (!renamingFolder || !name) return
    const r = await fetch('/api/kb/folders/' + encodeURIComponent(renamingFolder.kb_id), {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
    })
    const d = await r.json()
    if (d.ok) { message.success(`已改名为：${name}`); setRenamingFolder(null); setFolder(name); setFilterFolder(undefined); loadFolders(); loadDocs() } else { message.error(d.error?.message || '改名失败') }
  }

  async function deleteFolder(f) {
    Modal.confirm({
      title: `删除文件夹「${f.name}」`,
      content: '文件夹内的文档将一并删除，且不可恢复。确认删除？',
      okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        const r = await fetch('/api/kb/folders/' + encodeURIComponent(f.kb_id) + '?cascade=true', { method: 'DELETE' })
        const d = await r.json()
        if (d.ok) { message.success('已删除文件夹'); setFolder('default'); setFilterFolder(undefined); loadFolders(); loadDocs() } else { message.error(d.error?.message || '删除失败') }
      },
    })
  }

  async function moveDoc(docId, targetFolder) {
    const r = await fetch('/api/kb/docs/' + encodeURIComponent(docId) + '/folder', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kb_id: targetFolder }),
    })
    const d = await r.json()
    if (d.ok) { message.success('已移动文档'); loadDocs(); loadFolders() } else { message.error(d.error?.message || '移动失败') }
  }

  async function moveSelectedDocs() {
    if (!selectedIds.length || !batchMoveFolder) return
    const r = await fetch('/api/kb/docs/move', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ doc_ids: selectedIds, kb_id: batchMoveFolder }),
    })
    const d = await r.json()
    if (d.ok) { message.success(`已移动 ${d.data.moved} 篇文档`); setSelectedIds([]); setBatchMoveFolder(undefined); loadDocs(); loadFolders() } else { message.error(d.error?.message || '移动失败') }
  }

  async function uploadOne(file) {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('kb_id', folder || 'default')
    const r = await fetch('/api/kb/upload', { method: 'POST', body: fd })
    const d = await r.json()
    if (d.ok) { message.success(`已上传：${d.data.name}（${d.data.children} chunks）到 ${folder || 'default'}`) } else { message.error(`${file.name}: ` + (d.error?.message || '上传失败')) }
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || [])
    if (!files.length || uploading) return
    setUploading(true)
    let ok = 0
    for (let i = 0; i < files.length; i++) {
      setUploadProgress(`上传中 ${i + 1}/${files.length}: ${files[i].name}`)
      try { await uploadOne(files[i]); ok++ } catch (e) { message.error(`${files[i].name}: ` + e.message) }
    }
    setUploadProgress(`完成：${ok}/${files.length} 个文件`)
    setUploading(false)
    loadDocs(); loadFolders()
  }

  async function remove(docId) {
    await fetch('/api/kb/docs/' + docId, { method: 'DELETE' })
    message.success('已删除')
    loadDocs(); loadFolders()
  }

  return (
    <Card title="知识库管理" size="small">
      <Space direction="vertical" style={{ width: '100%' }}>
        <Space wrap>
          <Text strong>当前文件夹：</Text>
          <Select size="small" style={{ width: 180 }} value={folder} onChange={setFolder}
            options={folders.map((f) => ({ value: f.kb_id, label: f.name }))} />
          <Button size="small" disabled={folder === 'default'} onClick={() => { setRenamingFolder(folders.find((f) => f.kb_id === folder)); setRenameText(folder) }}>改名</Button>
          <Button size="small" danger disabled={folder === 'default'} onClick={() => deleteFolder(folders.find((f) => f.kb_id === folder))}>删除</Button>
        </Space>
        <Space wrap>
          <Text strong>新建文件夹：</Text>
          <Input size="small" style={{ width: 160 }} placeholder="新文件夹名" value={newFolder} onChange={(e) => setNewFolder(e.target.value)} onPressEnter={createFolder} />
          <Button size="small" onClick={createFolder}>新建</Button>
          <Button size="small" danger onClick={rebuildKb} loading={rebuilding}>重建法律库</Button>
          {rebuildMsg && <Text type="secondary">{rebuildMsg}</Text>}
        </Space>
        <Space wrap>
          <Text strong>批量移动：</Text>
          <Checkbox checked={docs.length > 0 && selectedIds.length === docs.length} onChange={(e) => setSelectedIds(e.target.checked ? docs.map((d) => d.doc_id) : [])}>全选</Checkbox>
          <Text type="secondary">已选 {selectedIds.length} 篇</Text>
          <Select size="small" style={{ width: 160 }} placeholder="目标文件夹" value={batchMoveFolder} onChange={setBatchMoveFolder}
            options={folders.map((f) => ({ value: f.kb_id, label: f.name }))} />
          <Button size="small" disabled={!selectedIds.length || !batchMoveFolder} onClick={moveSelectedDocs}>移动选中</Button>
        </Space>
        <Space wrap>
          <Button onClick={() => document.getElementById('kb-file-input').click()} disabled={uploading}>选择多个文件</Button>
          <Button onClick={() => document.getElementById('kb-folder-input').click()} disabled={uploading}>上传整个文件夹</Button>
          {uploading && <Tag color="blue">{uploadProgress}</Tag>}
          {!uploading && uploadProgress && <Text type="secondary">{uploadProgress}</Text>}
        </Space>
        <input id="kb-file-input" type="file" accept=".md,.txt,.docx,.pdf" multiple style={{ display: 'none' }}
          onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />
        <input id="kb-folder-input" type="file" webkitdirectory="" directory="" multiple style={{ display: 'none' }}
          onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />
        <Text type="secondary">支持 md / txt / docx / pdf（扫描件暂不支持）；可多选文件或整个文件夹上传。文件存于 data/uploads/，经解析→切块→索引入库。</Text>
        <Space wrap>
          <Text strong>按文件夹筛选：</Text>
          <Select size="small" allowClear style={{ width: 180 }} placeholder="全部文件夹" value={filterFolder} onChange={setFilterFolder}
            options={folders.map((f) => ({ value: f.kb_id, label: f.name }))} />
        </Space>
        <List size="small" dataSource={docs} renderItem={(d) => (
          <List.Item actions={[
            <Select key="mv" size="small" style={{ width: 130 }} value={d.kb_id || 'default'}
              onChange={(v) => moveDoc(d.doc_id, v)}
              options={folders.map((f) => ({ value: f.kb_id, label: f.name }))} />,
            <Button key="ch" size="small" onClick={() => viewChunks(d.doc_id)}>查看分块</Button>,
            <Button key="del" size="small" danger onClick={() => remove(d.doc_id)}>删除</Button>,
          ]}>
            <Checkbox checked={selectedIds.includes(d.doc_id)} onChange={(e) => setSelectedIds((prev) => e.target.checked ? [...prev, d.doc_id] : prev.filter((x) => x !== d.doc_id))} />
            <Text>{displayFileName(d.file_path)}</Text>
            <Tag color="blue">{d.kb_id || 'default'}</Tag>
            <Tag>{d.parse_status}</Tag>
            <Text type="secondary">{d.chunk_count} chunks</Text>
          </List.Item>
        )} />
        <Modal open={!!renamingFolder} onCancel={() => setRenamingFolder(null)} onOk={saveRenameFolder} okText="保存" cancelText="取消" title={`文件夹改名（${renamingFolder?.name || ''}）`}>
          <Input value={renameText} onChange={(e) => setRenameText(e.target.value)} placeholder="新文件夹名" />
        </Modal>
        <Modal open={!!chunkViewDoc} onCancel={() => setChunkViewDoc(null)} footer={null} title={`分块查看与调整（${chunkViewDoc || ''}）`} width={860}>
          <div style={{ maxHeight: 480, overflow: 'auto' }}>
            {chunks.length === 0 && <Text type="secondary">（无分块）</Text>}
            {chunks.map((c, i) => (
              <Card key={c.chunk_id} size="small" style={{ marginBottom: 8 }} title={
                <Space size={4} wrap>
                  <Text strong style={{ fontSize: 12 }}>#{i + 1}</Text>
                  <Tag>{c.chunk_level}</Tag>
                  {c.articles && <Tag color="blue">条{c.articles.join(',')}</Tag>}
                  {c.folder && <Tag color="orange">{c.folder}</Tag>}
                  <Text type="secondary" style={{ fontSize: 11 }}>{c.token_count} tokens</Text>
                </Space>
              } extra={
                <Space size={4}>
                  <Button size="small" type="link" onClick={() => { setEditingChunk(c); setEditText(c.text || '') }}>编辑</Button>
                  <Button size="small" type="link" onClick={() => openSplit(c)}>拆分</Button>
                  {i > 0 && <Button size="small" type="link" onClick={() => mergeChunkWithPrev(i)}>与上一块合并</Button>}
                  {chunks.length > 1 && <Button size="small" type="link" danger onClick={() => deleteChunk(c)}>删除</Button>}
                </Space>
              }>
                <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0, maxHeight: 160, overflow: 'auto' }}>{c.text}</pre>
              </Card>
            ))}
          </div>
        </Modal>
        <Modal open={!!editingChunk} onCancel={() => setEditingChunk(null)} onOk={saveChunkEdit} okText="保存" cancelText="取消" confirmLoading={chunkBusy} width={720} title="编辑分块文本">
          <Input.TextArea value={editText} onChange={(e) => setEditText(e.target.value)} autoSize={{ minRows: 8, maxRows: 24 }} />
          <Text type="secondary" style={{ fontSize: 12 }}>保存后该分块将按新文本重新嵌入。</Text>
        </Modal>
        <Modal open={!!splittingChunk} onCancel={() => setSplittingChunk(null)} onOk={saveChunkSplit} okText="拆分" cancelText="取消" confirmLoading={chunkBusy} width={720} title="拆分为两个分块">
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text strong>第 1 段：</Text>
            <Input.TextArea value={splitPart1} onChange={(e) => setSplitPart1(e.target.value)} autoSize={{ minRows: 4, maxRows: 12 }} />
            <Text strong>第 2 段：</Text>
            <Input.TextArea value={splitPart2} onChange={(e) => setSplitPart2(e.target.value)} autoSize={{ minRows: 4, maxRows: 12 }} />
          </Space>
        </Modal>
      </Space>
    </Card>
  )
}

function ContractPage() {
  const [contracts, setContracts] = useState([])
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState('')
  const [editingContract, setEditingContract] = useState(null)
  const [editFiles, setEditFiles] = useState([])
  const [editFile, setEditFile] = useState(undefined)
  const [editContent, setEditContent] = useState('')
  const [editSaving, setEditSaving] = useState(false)

  function loadContracts() {
    fetch('/api/contracts').then((r) => r.json()).then((d) => setContracts(d.data || []))
  }

  async function openEditor(c) {
    setEditingContract(c); setEditContent(''); setEditFile(undefined); setEditFiles([])
    try {
      const r = await fetch(`/api/contracts/${c.contract_id}/files`)
      const d = await r.json()
      if (d.ok) { setEditFiles(d.data || []); if (d.data?.length) setEditFile(d.data[0]) }
    } catch (e) { message.error('获取文件列表失败: ' + e.message) }
  }

  async function loadEditContent(file) {
    if (!editingContract || !file) return
    const r = await fetch(`/api/contracts/${editingContract.contract_id}/content?file=` + encodeURIComponent(file))
    const d = await r.json()
    if (d.ok) { setEditContent(d.data.content) } else { message.error(d.error?.message || '读取失败') }
  }

  async function saveEdit() {
    if (!editingContract || !editFile || editSaving) return
    setEditSaving(true)
    try {
      const r = await fetch(`/api/contracts/${editingContract.contract_id}/content`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file: editFile, content: editContent }),
      })
      const d = await r.json()
      if (d.ok) { message.success(`已保存编辑版：${d.data.file}`); setEditingContract(null); loadContracts() } else { message.error(d.error?.message || '保存失败') }
    } catch (e) { message.error('保存失败: ' + e.message) } finally { setEditSaving(false) }
  }
  useEffect(() => { loadContracts() }, [])

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || [])
    if (!files.length || uploading) return
    setUploading(true); setUploadMsg('上传中...')
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    try {
      const r = await fetch('/api/contracts/upload', { method: 'POST', body: fd })
      const d = await r.json()
      if (d.ok) {
        const ok = d.data.filter((x) => x.ok).length
        setUploadMsg(`完成：${ok}/${d.data.length} 份上传成功`)
        loadContracts()
      } else { message.error(d.error?.message || '上传失败'); setUploadMsg('') }
    } catch (e) { message.error('上传失败: ' + e.message); setUploadMsg('') } finally { setUploading(false) }
  }

  async function review(cid) {
    const r = await fetch(`/api/contracts/${cid}/review`, { method: 'POST' })
    const d = await r.json()
    if (d.ok) { message.success(`审查完成：${d.risk_count} 处风险（高${d.high}/中${d.medium}/低${d.low}）`); loadContracts() } else { message.error(d.error?.message || '审查失败') }
  }

  async function remove(cid) {
    await fetch(`/api/contracts/${cid}`, { method: 'DELETE' })
    message.success('已删除')
    loadContracts()
  }

  async function uploadSkill(fileList) {
    const files = Array.from(fileList || [])
    if (!files.length) return
    for (const f of files) {
      const fd = new FormData(); fd.append('file', f)
      const r = await fetch('/api/contracts/skills', { method: 'POST', body: fd })
      const d = await r.json()
      if (d.ok) { message.success(`已上传规则：${d.data.filename}`) } else { message.error(d.error?.message || '规则上传失败') }
    }
  }

  return (
    <Card title="合同审查" size="small">
      <Space direction="vertical" style={{ width: '100%' }}>
        <Space wrap>
          <Button onClick={() => document.getElementById('contract-file-input').click()} disabled={uploading}>上传合同（docx/pdf）</Button>
          <Button onClick={() => document.getElementById('contract-skill-input').click()} disabled={uploading}>上传审查规则（.jsonl）</Button>
          {uploadMsg && <Text type="secondary">{uploadMsg}</Text>}
        </Space>
        <input id="contract-file-input" type="file" accept=".docx,.pdf" multiple style={{ display: 'none' }}
          onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />
        <input id="contract-skill-input" type="file" accept=".jsonl" multiple style={{ display: 'none' }}
          onChange={(e) => { uploadSkill(e.target.files); e.target.value = '' }} />
        <Text type="secondary">上传后自动脱敏；审查报告为规则引擎生成，使用前须经执业律师核阅。</Text>
        <List size="small" dataSource={contracts} renderItem={(c) => (
          <List.Item actions={[
            <Button key="rv" size="small" type="primary" onClick={() => review(c.contract_id)}>审查</Button>,
            <Button key="ed" size="small" onClick={() => openEditor(c)}>编辑</Button>,
            <Button key="rp" size="small" href={`/api/contracts/${c.contract_id}/report`} target="_blank" disabled={!c.report_path}>报告</Button>,
            <Button key="dl" size="small" href={`/api/contracts/${c.contract_id}/download?kind=redacted`} target="_blank">脱敏版</Button>,
            <Button key="an" size="small" href={`/api/contracts/${c.contract_id}/download?kind=annotated`} target="_blank">批注版</Button>,
            <Button key="rs" size="small" href={`/api/contracts/${c.contract_id}/download?kind=restored`} target="_blank">还原版</Button>,
            <Button key="del" size="small" danger onClick={() => remove(c.contract_id)}>删除</Button>,
          ]}>
            <Text>{c.original_name}</Text>
            <Tag color="blue">{c.status}</Tag>
            <Text type="secondary">{c.risk_count} 处风险</Text>
          </List.Item>
        )} />
        <Modal open={!!editingContract} onCancel={() => setEditingContract(null)} onOk={saveEdit} okText="保存编辑版" cancelText="取消" confirmLoading={editSaving} width={820} title={`编辑合同（${editingContract?.original_name || ''}）`}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Space>
              <Text strong>选择文件：</Text>
              <Select size="small" style={{ width: 280 }} value={editFile} onChange={(v) => { setEditFile(v); loadEditContent(v) }} options={editFiles.map((f) => ({ value: f, label: f }))} />
            </Space>
            <Input.TextArea value={editContent} onChange={(e) => setEditContent(e.target.value)} autoSize={{ minRows: 12, maxRows: 30 }} />
            <Text type="secondary" style={{ fontSize: 12 }}>保存后生成「编辑版.md」，再次审查时会一并扫描。</Text>
          </Space>
        </Modal>
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
      <style>{`
        .md-body { word-break: break-word; }
        .md-body p { margin: 0 0 8px 0; }
        .md-body p:last-child { margin-bottom: 0; }
        .md-body h1, .md-body h2, .md-body h3, .md-body h4 { margin: 8px 0 6px 0; }
        .md-body ul, .md-body ol { margin: 4px 0 8px 0; padding-left: 20px; }
        .md-body table { border-collapse: collapse; width: 100%; margin: 8px 0; }
        .md-body th, .md-body td { border: 1px solid #d9d9d9; padding: 4px 8px; font-size: 13px; }
        .md-body code { background: #f5f5f5; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
        .md-body pre { background: #f5f5f5; padding: 8px 12px; border-radius: 6px; overflow: auto; font-size: 12px; }
        .md-body blockquote { border-left: 3px solid #d9d9d9; margin: 8px 0; padding-left: 12px; color: #666; }
      `}</style>
      <Typography.Title level={3} style={{ marginBottom: 16 }}>法律助手 Demo</Typography.Title>
      <Tabs defaultActiveKey="chat" items={[
        { key: 'chat', label: '知识库问答', children: <ChatPage /> },
        { key: 'assistant', label: '实务助手', children: <AssistantPage /> },
        { key: 'kb', label: '知识库管理', children: <KbPage /> },
        { key: 'contract', label: '合同审查', children: <ContractPage /> },
        { key: 'settings', label: '设置', children: <SettingsPage /> },
      ]} />
    </div>
  )
}
