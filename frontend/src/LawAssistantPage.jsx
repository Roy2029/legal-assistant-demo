import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Button, Card, Collapse, Input, List, Modal, Radio, Select, Space, Tabs, Tag, Typography, message,
} from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, SendOutlined, StopOutlined } from '@ant-design/icons'

const { Text, Paragraph } = Typography

function Markdown({ content, style }) {
  return (
    <div style={style} className="md-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || ''}</ReactMarkdown>
    </div>
  )
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

const MODE_OPTIONS = [
  { value: 'chat', label: '知识库问答' },
  { value: 'case_analysis', label: '智能分析' },
  { value: 'rag', label: '知识库助手' },
  { value: 'case', label: '类案助手' },
]

function modeKeyOf(s) {
  if (!s) return 'case_analysis'
  if (s.mode === 'chat') return 'chat'
  if (s.action === 'rag') return 'rag'
  if (s.action === 'case') return 'case'
  return 'case_analysis'
}

function modeLabelOf(s) {
  const found = MODE_OPTIONS.find((m) => m.value === modeKeyOf(s))
  return found ? found.label : '智能分析'
}

export default function LawAssistantPage() {
  const [sessions, setSessions] = useState([])
  const [sessionId, setSessionId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [running, setRunning] = useState(false)
  const [modeKey, setModeKey] = useState('case_analysis')
  const [trace, setTrace] = useState(null)
  const [citations, setCitations] = useState([])
  const [agentSteps, setAgentSteps] = useState([])
  const [finalReport, setFinalReport] = useState('')
  const [agentTraceFull, setAgentTraceFull] = useState(null)
  const [traceId, setTraceId] = useState(null)
  const [lastQuery, setLastQuery] = useState('')
  const [lastAnswer, setLastAnswer] = useState('')
  const [folderOptions, setFolderOptions] = useState([])
  const [folderFilter, setFolderFilter] = useState([])
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameText, setRenameText] = useState('')
  const [chunkModal, setChunkModal] = useState(null)
  const [fullChunk, setFullChunk] = useState(null)
  const abortRef = useRef(null)

  useEffect(() => { loadFolders(); loadSessions() }, [])

  async function loadFolders() {
    const r = await fetch('/api/kb/folders')
    const d = await r.json()
    if (d.ok) setFolderOptions((d.data || []).map((f) => ({ value: f.kb_id, label: f.name })))
  }

  async function createSession(mk) {
    const r = await fetch('/api/sessions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: '新会话', mode: mk === 'chat' ? 'chat' : 'assistant', action: mk === 'chat' ? '' : mk }),
    })
    const d = await r.json()
    if (!d.ok) { message.error(d.error?.message || '创建会话失败'); return }
    setSessionId(d.data.session_id)
    setModeKey(mk)
    setMessages([])
    setTrace(null)
    setCitations([])
    setAgentSteps([])
    setAgentTraceFull(null)
    setFinalReport('')
    await loadSessions()
  }

  async function loadSessions() {
    const r = await fetch('/api/sessions')
    const d = await r.json()
    if (!d.ok) return
    const ss = (d.data || []).filter((s) => s.mode === 'chat' || s.mode === 'assistant')
    if (!ss.length) {
      await createSession('case_analysis')
      return
    }
    setSessions(ss)
    const cur = ss.find((s) => s.session_id === sessionId) || ss[0]
    if (!sessionId || cur.session_id !== sessionId) {
      setSessionId(cur.session_id)
      setModeKey(modeKeyOf(cur))
      loadMessages(cur.session_id)
      loadTraces(cur.session_id)
    } else {
      setModeKey(modeKeyOf(cur))
    }
  }

  async function loadMessages(sid) {
    const r = await fetch('/api/sessions/' + sid + '/messages')
    const d = await r.json()
    if (d.ok) setMessages((d.data || []).map((m) => ({ role: m.role, content: m.content })))
  }

  function applyAgentTrace(traceObj) {
    if (!traceObj) { setAgentSteps([]); setAgentTraceFull(null); return }
    setAgentTraceFull(traceObj)
    const steps = []
    const rounds = traceObj.rounds || []
    for (const r of rounds) {
      if (r.think_present) steps.push({ step: '第' + r.iteration + '轮推理', summary: '模型已输出推理内容', status: 'done' })
      for (const tc of (r.tool_calls || [])) {
        steps.push({
          step: '工具 ' + tc.tool,
          summary: (tc.ok ? '成功' : '失败') + ' · ' + ((tc.summary || '') + '').slice(0, 200),
          status: tc.ok ? 'done' : 'tool',
        })
      }
    }
    for (const e of (traceObj.errors || [])) steps.push({ step: '异常', summary: e.error || e.type || JSON.stringify(e).slice(0, 120), status: 'running' })
    setAgentSteps(steps)
  }

  async function loadTraces(sid) {
    try {
      const r = await fetch('/api/sessions/' + sid + '/traces')
      const d = await r.json()
      if (d.ok) {
        const t = d.data || {}
        if (t.retrieval) setTrace(t.retrieval)
        if (t.agent) applyAgentTrace(t.agent)
        else if (!t.agent) { setAgentTraceFull(null); setAgentSteps([]) }
      }
    } catch (e) { /* ignore */ }
  }

  async function newSession() {
    await createSession(modeKey)
  }

  async function switchMode(mk) {
    if (mk === modeKey || running) return
    if (sessionId && messages.length === 0) {
      await fetch('/api/sessions/' + sessionId, { method: 'DELETE' }).catch(() => {})
    }
    await createSession(mk)
  }

  async function deleteSession() {
    if (!sessionId) return
    await fetch('/api/sessions/' + sessionId, { method: 'DELETE' })
    setSessionId(null)
    setMessages([])
    setTrace(null); setCitations([]); setAgentSteps([]); setFinalReport('')
    await loadSessions()
  }

  async function renameSession() {
    const title = renameText.trim()
    if (!title) return
    const r = await fetch('/api/sessions/' + sessionId, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) })
    const d = await r.json()
    if (d.ok) { message.success('已重命名'); setRenameOpen(false); loadSessions() } else { message.error(d.error?.message || '重命名失败') }
  }

  async function openFullChunk(c) {
    try {
      const r = await fetch('/api/chunk/' + encodeURIComponent(c.chunk_id))
      const d = await r.json()
      if (d.ok) { setFullChunk(d.data); return }
      setFullChunk({ text: (c.text || '') + '\n\n[全文加载失败：' + (d.error?.message || '未知错误') + '，以下为截断预览]', law_name: c.meta?.law_name, article_no: c.meta?.article_no, chunk_id: c.chunk_id })
    } catch (e) {
      setFullChunk({ text: (c.text || '') + '\n\n[全文加载失败：' + e.message + '，以下为截断预览]', law_name: c.meta?.law_name, article_no: c.meta?.article_no, chunk_id: c.chunk_id })
    }
  }

  function updateLastAssistant(content, pending) {
    setMessages((m) => {
      const next = [...m]
      if (next.length && next[next.length - 1].role === 'assistant') {
        next[next.length - 1] = { role: 'assistant', content, pending }
      } else {
        next.push({ role: 'assistant', content, pending })
      }
      return next
    })
  }

  async function runChat(q) {
    const ctrl = new AbortController()
    abortRef.current = ctrl
    let assistant = ''
    updateLastAssistant('', true)
    try {
      const resp = await fetch('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
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
            if (evt.type === 'session_start') setTraceId(evt.trace_id)
            else if (evt.type === 'llm_token') { assistant += evt.token; updateLastAssistant(assistant, false) }
            else if (evt.type === 'trace') setTrace(evt.trace)
            else if (evt.type === 'citation_check') setCitations(evt)
            else if (evt.type === 'final') {
              assistant = evt.answer
              setLastQuery(q); setLastAnswer(evt.answer)
              if (evt.citations) setCitations(evt.citations)
              updateLastAssistant(assistant, false)
            } else if (evt.type === 'error') updateLastAssistant('⚠️ ' + evt.message, false)
          } catch {}
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') updateLastAssistant('⚠️ 请求失败: ' + e.message, false)
    } finally {
      abortRef.current = null
      setRunning(false)
      loadMessages(sessionId)
    }
  }

  async function runAgent(q, mk) {
    const url = mk === 'case_analysis' ? '/api/assistant' : (mk === 'rag' ? '/api/rag-agent' : '/api/case-agent')
    const folders = folderFilter.length ? folderFilter : undefined
    const body = mk === 'case_analysis'
      ? { action: 'case_analysis', query: q, session_id: sessionId, folders }
      : mk === 'rag'
        ? { query: q, session_id: sessionId, folders }
        : { query: q, session_id: sessionId }
    updateLastAssistant('执行中…', true)
    let finalText = ''
    try {
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
            else if (evt.type === 'step_start') setAgentSteps((s) => [...s, { step: evt.step, status: 'running' }])
            else if (evt.type === 'tool_call') setAgentSteps((s) => [...s, { tool: evt.tool, params: evt.params, status: 'tool' }])
            else if (evt.type === 'tool_result') setAgentSteps((s) => [...s, { summary: evt.summary, status: 'done' }])
            else if (evt.type === 'agent_start') setAgentSteps((s) => [...s, { step: (evt.agent === 'knowledge' ? '知识库助手' : evt.agent === 'case' ? '类案助手' : evt.agent) + ' 启动', status: 'running' }])
            else if (evt.type === 'agent_think') setAgentSteps((s) => [...s, { step: '推理', summary: (evt.text || '').slice(0, 120), status: 'done' }])
            else if (evt.type === 'agent_tool_call') setAgentSteps((s) => [...s, { tool: evt.tool, params: evt.params, status: 'tool' }])
            else if (evt.type === 'agent_tool_result') setAgentSteps((s) => [...s, { summary: (evt.summary || '').slice(0, 160), status: 'done' }])
            else if (evt.type === 'agent_retry') setAgentSteps((s) => [...s, { step: '重试', summary: evt.reason, status: 'running' }])
            else if (evt.type === 'agent_report') setAgentSteps((s) => [...s, { step: '检索报告', summary: evt.answer || (evt.needs_human ? '需人工介入' : '已生成'), status: 'done' }])
            else if (evt.type === 'step_end') setAgentSteps((s) => [...s, { step: evt.step + ' 完成', summary: evt.summary, status: 'done' }])
            else if (evt.type === 'final') {
              finalText = evt.answer || ''
              setLastQuery(q); setLastAnswer(finalText)
              if (evt.report) setFinalReport(evt.report)
              updateLastAssistant(finalText + (evt.report ? '\n\n---\n' + evt.report : ''), false)
            } else if (evt.type === 'error') updateLastAssistant('⚠️ ' + evt.message, false)
          } catch {}
        }
      }
    } catch (e) {
      updateLastAssistant('⚠️ 请求失败: ' + e.message, false)
    } finally {
      setRunning(false)
      if (sessionId) { loadMessages(sessionId); loadSessions() }
    }
  }

  async function send() {
    const q = input.trim()
    if (!q || running) return
    setInput('')
    setTrace(null)
    setCitations([])
    setAgentSteps([])
    setFinalReport('')
    setMessages((m) => [...m, { role: 'user', content: q }])
    setRunning(true)
    if (modeKey === 'chat') await runChat(q)
    else await runAgent(q, modeKey)
  }

  function stop() {
    if (abortRef.current) abortRef.current.abort()
  }

  const retrievalTraceView = (
    !trace ? <Text type="secondary">知识库问答发送问题后展示检索过程</Text> : (
      <Collapse size="small" defaultActiveKey={['final']} items={[
        { key: 'final', label: `最终上下文（RRF top-${trace.final_count}，难度：${trace.difficulty?.level || '-'}）`, children: <ChunkList items={trace.final_topk || []} onOpenFull={openFullChunk} /> },
        { key: 'rrf_raw', label: 'RRF 原始 top-10（未按难度截断）', children: <ChunkList items={trace.rrf_raw_topk || []} onOpenFull={openFullChunk} /> },
        { key: 'parsed', label: 'Query 解析', children: <pre style={{ fontSize: 12 }}>{JSON.stringify(trace.parsed, null, 2)}</pre> },
        { key: 'difficulty', label: '难度分档', children: <pre style={{ fontSize: 12 }}>{JSON.stringify(trace.difficulty, null, 2)}</pre> },
        { key: 'bm25_tokens', label: 'BM25 查询分词', children: (
          <div>
            {(trace.bm25_tokens || []).map((t, i) => <Tag key={i} style={{ marginBottom: 4 }}>{t}</Tag>)}
            <Text type="secondary" style={{ display: 'block', marginTop: 4, fontSize: 12 }}>自定义关键词生效：分词中包含完整词条即生效</Text>
          </div>
        ) },
        { key: 'dense', label: `Dense 召回（${(trace.dense_topk || []).length}）`, children: <ChunkList items={trace.dense_topk || []} onOpenFull={openFullChunk} /> },
        { key: 'bm25', label: `BM25 召回（${(trace.bm25_topk || []).length}）`, children: <ChunkList items={trace.bm25_topk || []} onOpenFull={openFullChunk} /> },
      ]} />
    )
  )

  const agentTraceView = (
    <div>
      {!agentSteps.length && <Text type="secondary">智能分析 / 知识库助手 / 类案助手 执行后展示思考与工具 trace</Text>}
      {agentSteps.length > 0 && (
        <List
          size="small"
          dataSource={agentSteps}
          renderItem={(s, i) => (
            <List.Item key={i} style={{ display: 'block' }}>
              <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12, lineHeight: 1.6 }}>
                <Tag color={s.status === 'done' ? 'green' : 'blue'} style={{ marginRight: 6 }}>{s.status === 'done' ? '完成' : '进行中'}</Tag>
                <Text style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12 }}>{(s.step || s.tool || '') + (s.summary ? '：' + s.summary : '')}</Text>
              </div>
            </List.Item>
          )}
        />
      )}
      {finalReport && (
        <Card size="small" style={{ marginTop: 8 }} title="执行报告">
          <Markdown content={finalReport} style={{ fontSize: 12 }} />
        </Card>
      )}
      {agentTraceFull && (
        <Collapse
          size="small"
          style={{ marginTop: 8 }}
          items={[{ key: 'raw', label: '原始 trace JSON', children: <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 11, maxHeight: 300, overflow: 'auto' }}>{JSON.stringify(agentTraceFull, null, 2)}</pre> }]}
        />
      )}
    </div>
  )

  return (
    <div style={{ display: 'flex', gap: 8, height: 'calc(100vh - 120px)' }}>
      {/* 左：会话列表 */}
      <Card
        size="small"
        style={{ width: 240, flexShrink: 0, display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, overflow: 'auto', padding: 8 } }}
        title="会话列表"
        extra={<Button size="small" type="primary" icon={<PlusOutlined />} onClick={newSession}>新建会话</Button>}
      >
        <List
          size="small"
          dataSource={sessions}
          locale={{ emptyText: '暂无会话' }}
          renderItem={(s) => (
            <List.Item
              style={{ cursor: 'pointer', background: s.session_id === sessionId ? '#e6f4ff' : undefined, padding: '6px 8px', borderRadius: 6, marginBottom: 2 }}
              onClick={() => { setSessionId(s.session_id); setModeKey(modeKeyOf(s)); setMessages([]); loadMessages(s.session_id); setTrace(null); setCitations([]); setAgentSteps([]); setAgentTraceFull(null); setFinalReport(''); loadTraces(s.session_id) }}
              actions={[
                <Button key="rn" size="small" type="text" icon={<EditOutlined />} onClick={(e) => { e.stopPropagation(); setRenameText(s.title || ''); setRenameOpen(true) }} />,
                <Button key="dl" size="small" type="text" danger icon={<DeleteOutlined />} onClick={(e) => { e.stopPropagation(); if (s.session_id === sessionId) deleteSession(); else { fetch('/api/sessions/' + s.session_id, { method: 'DELETE' }).then(loadSessions) } }} />,
              ]}
            >
              <Space direction="vertical" size={0} style={{ width: '100%' }}>
                <Text style={{ fontSize: 13, wordBreak: 'break-all' }}>{s.title || s.session_id.slice(0, 8)}</Text>
                <Tag color={s.mode === 'chat' ? 'green' : 'blue'} style={{ fontSize: 11 }}>{modeLabelOf(s)}</Tag>
              </Space>
            </List.Item>
          )}
        />
      </Card>

      {/* 中：对话窗口 */}
      <Card size="small" style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }} styles={{ body: { flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', padding: 8 } }}>
        <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
          {messages.map((m, i) => (
            <div key={i} style={{ marginBottom: 12, textAlign: m.role === 'user' ? 'right' : 'left' }}>
              <Tag color={m.role === 'user' ? 'blue' : 'green'}>{m.role === 'user' ? '你' : '助手'}</Tag>
              <div style={{ display: 'inline-block', maxWidth: '82%', textAlign: 'left', background: m.role === 'user' ? '#e6f7ff' : '#f6ffed', padding: '8px 12px', borderRadius: 8, verticalAlign: 'top' }}>
                {m.role === 'assistant'
                  ? <Markdown content={m.content || (m.pending ? '思考中...' : '')} style={{ whiteSpace: 'normal' }} />
                  : <div style={{ whiteSpace: 'pre-wrap' }}>{m.content || ''}</div>}
              </div>
            </div>
          ))}
          {lastAnswer && !running && (
            <div style={{ padding: '0 8px 4px' }}>
              <Feedback mode={modeKey === 'chat' ? 'chat' : 'assistant'} action={modeKey === 'chat' ? '' : modeKey} sessionId={sessionId} traceId={traceId} query={lastQuery} answer={lastAnswer} trace={trace} />
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
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4, flexWrap: 'wrap' }}>
          <Select size="small" mode="multiple" allowClear style={{ minWidth: 240, flex: 1 }} placeholder="全部知识库（公共+本人）" value={folderFilter} onChange={setFolderFilter}
            options={[{ value: '__public__', label: '公共法律库' }, ...folderOptions]} disabled={modeKey === 'case'} />
          <Text type="secondary" style={{ fontSize: 12 }}>会话请从左侧列表选择</Text>
        </div>
        <div style={{ display: 'flex', gap: 8, paddingTop: 8 }}>
          <Input.TextArea value={input} onChange={(e) => setInput(e.target.value)} onPressEnter={(e) => { e.preventDefault(); send() }} placeholder={modeKey === 'chat' ? '输入法律问题，如：民法典第580条说了什么' : '输入法律问题或案情要点'} autoSize={{ minRows: 2, maxRows: 6 }} disabled={running} />
          <Space>
            {running ? <Button danger icon={<StopOutlined />} onClick={stop}>停止</Button> : <Button type="primary" icon={<SendOutlined />} onClick={send}>发送</Button>}
          </Space>
        </div>
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', paddingTop: 6, flexWrap: 'wrap' }}>
          <Text type="secondary" style={{ fontSize: 12, marginRight: 4 }}>模式：</Text>
          <Radio.Group
            size="small"
            value={modeKey}
            onChange={(e) => switchMode(e.target.value)}
            optionType="button"
            buttonStyle="solid"
            options={MODE_OPTIONS}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>切换模式将开启新会话（默认智能分析）</Text>
        </div>
      </Card>

      {/* 右：trace 双 tab */}
      <Card size="small" style={{ width: 420, flexShrink: 0, display: 'flex', flexDirection: 'column' }} styles={{ body: { flex: 1, overflow: 'auto', padding: 8 } }} title="Trace">
        <Tabs
          size="small"
          items={[
            { key: 'retrieval', label: '检索trace', children: retrievalTraceView },
            { key: 'agent', label: 'Agent trace', children: agentTraceView },
          ]}
        />
        {citations && citations.unverifiable && (
          <Paragraph style={{ marginTop: 12 }} type="warning">
            ⚠️ 未能验证的引用：{citations.unverifiable.join('、')}
          </Paragraph>
        )}
      </Card>

      <Modal open={!!fullChunk} onCancel={() => setFullChunk(null)} footer={null} title={fullChunk ? `${fullChunk.law_name || ''} ${fullChunk.article_no ? '第' + fullChunk.article_no + '条' : ''} ${fullChunk.chunk_id || ''}`.trim() : ''} width={720}>
        <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 480, overflow: 'auto', fontSize: 13 }}>{fullChunk?.text}</pre>
      </Modal>
      <Modal open={renameOpen} onCancel={() => setRenameOpen(false)} onOk={renameSession} okText="保存" cancelText="取消" title="会话重命名">
        <Input value={renameText} onChange={(e) => setRenameText(e.target.value)} placeholder="会话标题" />
      </Modal>
      <Modal open={!!chunkModal} onCancel={() => setChunkModal(null)} footer={null} title={chunkModal ? `${chunkModal.law_name} 第${chunkModal.article_no}条` : ''}>
        <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 400, overflow: 'auto', fontSize: 13 }}>{chunkModal?.text}</pre>
      </Modal>
    </div>
  )
}
